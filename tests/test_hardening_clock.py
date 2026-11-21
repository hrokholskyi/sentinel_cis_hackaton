"""The simulated clock needs X-Sim-Token (security hardening, finding 4)."""

from datetime import datetime, timedelta

import httpx

import common
from hardening_helpers import pay, q

DORMANT = {"account_id": common.ACCOUNT_ID, "beneficiary_id": "ben_dorm_01",
           "amount_aed": 4500, "purpose": common.ATTACK_PURPOSE}


def test_client_cannot_choose_its_own_clock(stack):
    """An attacker gives every payment another daytime hour; without the token that is ignored."""
    start = datetime.combine(common.DEMO_DATE - timedelta(days=30),
                             datetime.strptime("10:00:00", "%H:%M:%S").time())
    codes = []
    for i in range(20):
        ts = start + timedelta(days=i)
        r = httpx.post(stack.breaker_url + "/payouts", json=DORMANT, timeout=10,
                       headers={"X-API-Key": common.API_KEY_SECRET, "X-Sim-Time": common.iso(ts)})
        codes.append(r.status_code)
    assert codes.count(200) == 8, codes
    s = stack.get("/state")
    assert s["keys"][0]["state"] == "paused"
    assert any("X-Sim-Time" in e["text"] and "ignored" in e["text"] for e in s["events"])
    times = {r[0] for r in q(stack.env["BREAKER_DB"], "SELECT created_at FROM reservations")}
    assert not any(t.endswith("10:00:00") for t in times)


def test_wrong_token_is_ignored(stack):
    ts = datetime.combine(common.LEGIT_DATE, datetime.strptime("10:00:00", "%H:%M:%S").time())
    body = dict(DORMANT, amount_aed=800)
    r = pay(stack.breaker_url, ts, body, token="not-the-token")
    assert r.status_code == 200, r.text
    created = q(stack.env["BREAKER_DB"], "SELECT created_at FROM reservations")[0][0]
    assert created != ts.strftime("%Y-%m-%d %H:%M:%S")


def test_valid_token_is_honoured_and_forwarded_to_the_bank(stack):
    ts = datetime.combine(common.LEGIT_DATE, datetime.strptime("10:00:00", "%H:%M:%S").time())
    r = pay(stack.breaker_url, ts, dict(DORMANT, amount_aed=800))
    assert r.status_code == 200, r.text
    want = ts.strftime("%Y-%m-%d %H:%M:%S")
    assert q(stack.env["BREAKER_DB"], "SELECT created_at FROM reservations")[0][0] == want
    assert r.json()["created_at"] == want  # the bank took the same clock


def test_bank_ignores_sim_time_without_token(stack):
    ts = datetime.combine(common.LEGIT_DATE, datetime.strptime("10:00:00", "%H:%M:%S").time())
    body = dict(DORMANT, amount_aed=800)
    r = httpx.post(stack.bank_url + "/payouts", json=body, timeout=10,
                   headers={"X-API-Key": common.API_KEY_SECRET, "X-Sim-Time": common.iso(ts)})
    assert r.status_code == 200, r.text
    assert r.json()["created_at"] != ts.strftime("%Y-%m-%d %H:%M:%S")
    r = httpx.post(stack.bank_url + "/payouts", json=body, timeout=10,
                   headers={"X-API-Key": common.API_KEY_SECRET, "X-Sim-Time": common.iso(ts),
                            "X-Sim-Token": common.SIM_TOKEN})
    assert r.json()["created_at"] == ts.strftime("%Y-%m-%d %H:%M:%S")


def test_demo_mode_off_ignores_sim_time_even_with_token(make_stack):
    stack = make_stack(env={"DEMO_MODE": "0"})
    ts = datetime.combine(common.LEGIT_DATE, datetime.strptime("10:00:00", "%H:%M:%S").time())
    r = pay(stack.breaker_url, ts, dict(DORMANT, amount_aed=800))
    assert r.status_code == 200, r.text
    assert r.json()["created_at"] != ts.strftime("%Y-%m-%d %H:%M:%S")
    created = q(stack.env["BREAKER_DB"], "SELECT created_at FROM reservations")[0][0]
    assert created != ts.strftime("%Y-%m-%d %H:%M:%S")
