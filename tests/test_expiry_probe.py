"""Milestone 6: lazy hold expiry on the request clock, and the probe state."""

import sqlite3
from datetime import datetime, timedelta

import httpx

import common
import replaylib
from conftest import baseline_rules_dir

ADMIN = {"X-Admin-Token": "dev-admin-token"}
NIGHT = datetime.combine(common.DEMO_DATE, datetime.min.time())  # D 00:00
DORMANT = {"account_id": common.ACCOUNT_ID, "beneficiary_id": "ben_dorm_01",
           "amount_aed": 11750, "purpose": common.ATTACK_PURPOSE}


def pay(stack, ts: datetime, body: dict) -> dict:
    r = httpx.post(stack.breaker_url + "/payouts", json=body, timeout=10,
                   headers={"X-API-Key": common.API_KEY_SECRET, "X-Sim-Time": common.iso(ts),
                            "X-Sim-Token": common.SIM_TOKEN})
    assert r.status_code in (200, 202), r.text
    return r.json()


def small(ts_ben="ben_reg_01", amount=800) -> dict:
    return {"account_id": common.ACCOUNT_ID, "beneficiary_id": ts_ben, "amount_aed": amount,
            "purpose": "Small top-up"}


def verdict(stack, name: str, incident_id: str = "inc_0001", sim=None) -> httpx.Response:
    headers = dict(ADMIN)
    if sim:
        headers["X-Sim-Time"] = common.iso(sim)
        headers["X-Sim-Token"] = common.SIM_TOKEN
    return httpx.post(f"{stack.breaker_url}/incidents/{incident_id}/verdict", headers=headers,
                      json={"incident_id": incident_id, "verdict": name,
                            "reason": "Synthetic test verdict."}, timeout=30)


def q(stack, sql, *args):
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    conn.execute("ATTACH DATABASE ? AS bank", (stack.env["BANK_DB"],))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run(stack, script):
    r = stack.run_script(script, "--delay", "0", "--trace", str(stack.tmp / f"{script}.jsonl"))
    assert r.returncode == 0, r.stdout + r.stderr


def main_demo(stack):
    """Legit, soft, attack, then a contact_client verdict, as in the guided demo."""
    for script in ("traffic_legit.py", "traffic_soft.py", "traffic_attack.py"):
        run(stack, script)
    assert verdict(stack, "contact_client").status_code == 200


def bank_payments(stack) -> int:
    return q(stack, "SELECT COUNT(*) FROM bank.payments")[0][0]


def test_main_demo_with_verdicts_expires_nothing(stack):
    main_demo(stack)
    out = verdict(stack, "hold_until_morning").json()
    assert out["hold_until"] == "2026-11-21 08:00:00"
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE status = 'rejected'") == [(0,)]
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE status = 'held'"
             " AND incident_id = 'inc_0001'") == [(36,)]
    s = stack.get("/state")
    assert s["key"]["state"] == "paused" and s["incident"]["status"] == "open"
    assert s["counters"]["stopped"] == {"count": 36, "aed": 423000}
    assert s["counters"]["expired"] == {"count": 0, "aed": 0}
    assert not [e for e in s["events"] if e["kind"] == "expiry"]


def test_later_request_expires_holds_and_key_moves_to_probe(stack):
    main_demo(stack)
    before = bank_payments(stack)
    # GET /state never expires anything, even long after the limit.
    stack.get("/state")
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE status = 'held'") == [(36,)]

    late = pay(stack, NIGHT.replace(hour=2, minute=40), DORMANT)
    assert (late["status"], late["reason"], late["rule_id"]) == ("held", "probe", "probe")
    assert late["detail"] == "probe: amount 11750 above 1000"
    assert q(stack, "SELECT rule_id, level FROM reservations WHERE id = ?",
             late["reservation_id"]) == [("probe", "hard")]

    assert q(stack, "SELECT COUNT(*), MIN(decided_at), MAX(decided_at) FROM reservations"
             " WHERE status = 'rejected' AND decided_by = 'expiry'") == \
        [(36, "2026-11-21 02:40:00", "2026-11-21 02:40:00")]
    assert q(stack, "SELECT COUNT(*) FROM events WHERE kind = 'expiry'"
             " AND text LIKE 'hold res_% expired%'") == [(36,)]
    assert bank_payments(stack) == before  # nothing sent to the bank

    s = stack.get("/state")
    assert s["key"]["state"] == "probe" and s["key"]["incident_id"] == "inc_0001"
    assert s["key"]["probe_clean"] == 0 and s["key"]["probe_target"] == 5
    assert s["incidents"][0]["status"] == "expired" and s["open_incident"] is None
    assert s["incident"]["expired_count"] == 36 and s["incident"]["held_count"] == 0
    assert s["counters"]["expired"] == {"count": 36, "aed": 423000}
    assert s["counters"]["stopped"] == {"count": 1, "aed": 11750}  # only the probe hold
    assert s["layers"]["layer1"]["result"] == "probe"
    texts = [e["text"] for e in s["events"]]
    assert any("moved to probe" in t for t in texts)

    # A verdict for an incident whose holds all expired fails cleanly.
    r = verdict(stack, "release")
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert bank_payments(stack) == before


def test_hold_until_morning_and_backward_clock_delay_expiry(stack):
    for script in ("traffic_legit.py", "traffic_attack.py"):
        run(stack, script)
    assert verdict(stack, "hold_until_morning").json()["hold_until"] == "2026-11-21 08:00:00"

    # The clock goes back to the legit day: nothing expires, the key stays paused.
    back = pay(stack, datetime.combine(common.LEGIT_DATE, datetime.min.time()).replace(hour=15),
               DORMANT)
    assert back["reason"] == "key_paused"
    # 02:40 is past the 30 minute limit but before hold_until.
    at_240 = pay(stack, NIGHT.replace(hour=2, minute=40), DORMANT)
    assert at_240["reason"] == "key_paused"
    # Exactly 08:00:00 is not later than hold_until; the 02:40 hold runs until 03:10.
    at_8 = pay(stack, NIGHT.replace(hour=8), DORMANT)
    assert at_8["reason"] == "key_paused"
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE status = 'rejected'") == [(0,)]
    assert stack.get("/state")["key"]["state"] == "paused"

    # A verdict with a request clock past the deadline expires the holds and fails cleanly.
    r = verdict(stack, "contact_client", sim=NIGHT.replace(hour=8, minute=31))
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE status = 'rejected'"
             " AND decided_by = 'expiry'") == [(36 + 3,)]
    assert stack.get("/state")["key"]["state"] == "probe"


def test_admin_call_runs_expiry_on_the_request_clock(stack):
    for script in ("traffic_legit.py", "traffic_attack.py"):
        run(stack, script)
    held = q(stack, "SELECT id FROM reservations WHERE status = 'held' ORDER BY id LIMIT 1")[0][0]
    # No X-Sim-Time: the request clock is the key's latest request, so nothing expires.
    r = httpx.post(f"{stack.breaker_url}/admin/holds/{held}/reject", headers=ADMIN, timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert q(stack, "SELECT decided_by FROM reservations WHERE id = ?", held) == [("admin",)]
    other = q(stack, "SELECT id FROM reservations WHERE status = 'held' ORDER BY id LIMIT 1")[0][0]
    late = {**ADMIN, "X-Sim-Time": common.iso(NIGHT.replace(hour=3)),
            "X-Sim-Token": common.SIM_TOKEN}
    r = httpx.post(f"{stack.breaker_url}/admin/holds/{other}/release", headers=late, timeout=10)
    assert r.status_code == 409 and "rejected" in r.json()["detail"]
    assert q(stack, "SELECT status, decided_by FROM reservations WHERE id = ?", other) == \
        [("rejected", "expiry")]
    assert stack.get("/state")["key"]["state"] == "probe"


def test_probe_returns_to_normal_after_five_clean_payments_across_restart(make_stack, tmp_path):
    s = make_stack()
    for script in ("traffic_legit.py", "traffic_attack.py"):
        run(s, script)
    t = NIGHT.replace(hour=2, minute=40)
    assert pay(s, t, DORMANT)["reason"] == "probe"
    assert s.get("/state")["key"]["state"] == "probe"
    before = bank_payments(s)

    # Above AED 1,000: held, even to a beneficiary with history.
    assert pay(s, t + timedelta(minutes=1), small(amount=1000.5))["reason"] == "probe"
    salary_history = pay(s, t + timedelta(minutes=1, seconds=5), small("ben_emp_01", 500))
    assert salary_history["status"] == "executed"
    for i in range(2):
        r = pay(s, t + timedelta(minutes=2 + i), small())
        assert r["status"] == "executed"
    assert s.get("/state")["key"]["probe_clean"] == 3

    # The count lives in the database: restart the breaker on the same databases.
    s.stop()
    with replaylib.start(s.workdir, seed_first=False,
                         rules_dir=baseline_rules_dir(tmp_path / "rules_restart")) as s2:
        st = s2.get("/state")["key"]
        assert (st["state"], st["probe_clean"]) == ("probe", 3)
        assert pay(s2, t + timedelta(minutes=4), small())["status"] == "executed"
        assert s2.get("/state")["key"]["state"] == "probe"
        assert pay(s2, t + timedelta(minutes=5), small())["status"] == "executed"
        st = s2.get("/state")
        assert (st["key"]["state"], st["key"]["probe_clean"]) == ("normal", 0)
        assert any("back to normal after 5 clean payments" in e["text"] for e in st["events"])
        assert q(s2, "SELECT COUNT(*) FROM reservations WHERE decided_by = 'probe'"
                 " AND status = 'executed'") == [(5,)]
        assert bank_payments(s2) == before + 5
        # Normal again: a payment above AED 1,000 passes the rules as usual.
        assert pay(s2, t + timedelta(minutes=20), small(amount=4000))["status"] == "executed"


def test_new_beneficiary_is_held_in_probe_and_hard_rule_pauses_again(stack):
    # Trip burst_count_5m on the legit day with no legit run, so the new suppliers have no history.
    start = datetime.combine(common.LEGIT_DATE, datetime.min.time()).replace(hour=10)
    plan = [b for _, b in common.LEGIT_PLAN if b["beneficiary_id"].startswith("ben_reg")]
    for i, b in enumerate(plan[:9]):
        pay(stack, start + timedelta(seconds=i), b)
    assert stack.get("/state")["key"]["state"] == "paused"

    t = start + timedelta(minutes=31)
    r = pay(stack, t, small("ben_new_01", 500))
    assert (r["reason"], r["detail"]) == ("probe", "probe: beneficiary has no prior payments")
    assert q(stack, "SELECT COUNT(*) FROM reservations WHERE decided_by = 'expiry'") == [(1,)]
    assert stack.get("/state")["key"]["state"] == "probe"

    # Probe holds still count for the rules: the 9th reservation in 5 minutes trips the burst rule.
    for i in range(1, 8):
        assert pay(stack, t + timedelta(seconds=i), {**DORMANT, "amount_aed": 6000}
                   )["reason"] == "probe"
    tripped = pay(stack, t + timedelta(seconds=8), small())
    assert (tripped["reason"], tripped["rule_id"]) == ("hard_stop", "burst_count_5m")
    s = stack.get("/state")
    assert s["key"]["state"] == "paused" and s["key"]["incident_id"] == "inc_0002"
    assert s["open_incident"]["id"] == "inc_0002"
    assert [i["status"] for i in s["incidents"]] == ["expired", "open"]
    assert pay(stack, t + timedelta(seconds=9), small())["reason"] == "key_paused"
