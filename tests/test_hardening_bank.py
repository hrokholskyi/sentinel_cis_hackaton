"""Unclear bank outcomes are never paid twice (security hardening, findings 6, 9 and 10)."""

import sqlite3
import threading
from datetime import datetime

import httpx

import common
import replaylib
from conftest import baseline_rules_dir
from hardening_helpers import (ADMIN, DRAIN, SOFT, SOFT_AT, BankProxy, extra_breaker, pay, q,
                               wait_for)

DAY = datetime.combine(common.LEGIT_DATE, datetime.strptime("12:00:00", "%H:%M:%S").time())
SMALL = {"account_id": common.ACCOUNT_ID, "beneficiary_id": "ben_reg_01", "amount_aed": 777,
         "purpose": "Small freight top-up"}


def test_bank_returns_the_stored_payment_for_a_repeated_idempotency_key(stack):
    headers = {"X-API-Key": common.API_KEY_SECRET, "Idempotency-Key": "res_test_1"}
    a = httpx.post(stack.bank_url + "/payouts", json=SMALL, headers=headers, timeout=10)
    b = httpx.post(stack.bank_url + "/payouts", json=SMALL, headers=headers, timeout=10)
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["payment_id"] == b.json()["payment_id"]
    assert len(q(stack.env["BANK_DB"], "SELECT * FROM payments WHERE amount_aed = 777")) == 1
    assert a.json()["balance_aed"] == b.json()["balance_aed"]


def test_lost_bank_answer_is_not_paid_twice(stack):
    with BankProxy(drop=1) as proxy:
        proxy.target = stack.bank_url
        with extra_breaker(stack, BANK_URL=proxy.url) as url:
            r = pay(url, DAY, SMALL)
            assert r.status_code == 502 and r.json()["reason"] == "bank_unavailable", r.text
            res_id = r.json()["reservation_id"]
            rel = httpx.post(f"{url}/admin/holds/{res_id}/release", headers=ADMIN, timeout=30)
            assert rel.status_code == 200, rel.text
    paid = q(stack.env["BANK_DB"], "SELECT * FROM payments WHERE amount_aed = 777")
    assert len(paid) == 1
    res = q(stack.env["BREAKER_DB"], "SELECT * FROM reservations WHERE id = ?", res_id)[0]
    assert res["status"] == "executed" and res["payment_id"] == paid[0]["id"]
    assert rel.json()["payment_id"] == paid[0]["id"]
    assert proxy.calls == 1  # the release found the payment and did not call the bank again


def test_restart_reconciles_rows_that_were_in_flight(tmp_path):
    work = tmp_path / "stack"
    work.mkdir()
    env = replaylib.base_env(work)
    replaylib.seed(env)
    conn = sqlite3.connect(env["BREAKER_DB"])
    for res_id, status in (("res_000001", "released"), ("res_000002", "pending")):
        conn.execute("INSERT INTO reservations (id, api_key_id, account_id, beneficiary_id,"
                     " amount_aed, purpose, status, created_at, decided_by)"
                     " VALUES (?, ?, ?, 'ben_reg_01', 777, 'x', ?, '2026-11-20 12:00:00', 'admin')",
                     (res_id, common.API_KEY_ID, common.ACCOUNT_ID, status))
    conn.commit()
    conn.close()
    bank = sqlite3.connect(env["BANK_DB"])
    bank.execute("INSERT INTO payments (id, account_id, beneficiary_id, api_key_id, amount_aed,"
                 " purpose, created_at, idempotency_key) VALUES ('pay_900001', ?, 'ben_reg_01', ?,"
                 " 777, 'x', '2026-11-20 12:00:00', 'res_000002')",
                 (common.ACCOUNT_ID, common.API_KEY_ID))
    bank.commit()
    bank.close()
    with replaylib.start(work, seed_first=False,
                         rules_dir=baseline_rules_dir(tmp_path / "rules")) as stack:
        events = [e["text"] for e in stack.get("/state")["events"]]
    rows = {r["id"]: r for r in q(env["BREAKER_DB"], "SELECT * FROM reservations")}
    assert rows["res_000001"]["status"] == "held"
    assert rows["res_000001"]["rule_id"] == "restart_unclear"
    assert rows["res_000002"]["status"] == "executed"
    assert rows["res_000002"]["payment_id"] == "pay_900001"
    assert any("res_000001" in e and "restart" in e for e in events)


def test_verdict_does_not_reopen_an_incident_closed_during_the_release(stack, tmp_path):
    """An admin resume closes the incident while a release verdict is sending payments."""
    with BankProxy(delay=2.0) as proxy:
        proxy.target = stack.bank_url
        dead = replaylib.free_port()  # nothing listens: Jev gives no answer, the hold stays
        with extra_breaker(stack, BANK_URL=proxy.url, JEV_MODE="live",
                           TYPESAFE_API_KEY="dummy-key",
                           JEV_URL=f"http://127.0.0.1:{dead}/v1/systemone") as url:
            assert pay(url, SOFT_AT, SOFT).json()["reason"] == "soft_stop"
            assert pay(url, SOFT_AT.replace(second=5), DRAIN).json()["reason"] == "hard_stop"
            out = []
            t = threading.Thread(target=lambda: out.append(httpx.post(
                f"{url}/incidents/inc_0001/verdict", headers=ADMIN, timeout=30,
                json={"incident_id": "inc_0001", "verdict": "release",
                      "reason": "Synthetic test verdict."})))
            t.start()
            wait_for(lambda: proxy.calls == 1)
            r = httpx.post(f"{url}/admin/keys/{common.API_KEY_ID}/resume", headers=ADMIN,
                           timeout=30)
            assert r.status_code == 200, r.text
            t.join()
            assert out[0].status_code == 200, out[0].text
            s = httpx.get(url + "/state", timeout=10).json()
    inc = q(stack.env["BREAKER_DB"], "SELECT * FROM incidents WHERE id = 'inc_0001'")[0]
    assert inc["status"] == "closed"
    assert s["keys"][0]["state"] == "normal"
