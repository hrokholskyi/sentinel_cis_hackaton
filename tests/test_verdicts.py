import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

import httpx

import common
import replaylib

ADMIN = {"X-Admin-Token": "dev-admin-token"}


def pay(stack, ts: datetime, body: dict) -> dict:
    r = httpx.post(stack.breaker_url + "/payouts", json=body, timeout=10,
                   headers={"X-API-Key": common.API_KEY_SECRET, "X-Sim-Time": common.iso(ts),
                            "X-Sim-Token": common.SIM_TOKEN})
    assert r.status_code in (200, 202), r.text
    return r.json()


def verdict(stack, incident_id: str, body: dict, headers=ADMIN) -> httpx.Response:
    return httpx.post(f"{stack.breaker_url}/incidents/{incident_id}/verdict", json=body,
                      headers=headers, timeout=30)


def body(verdict_name: str, incident_id: str = "inc_0001", **extra) -> dict:
    return {"incident_id": incident_id, "verdict": verdict_name,
            "reason": "Synthetic test verdict.", **extra}


def bank(stack, sql, *args):
    conn = sqlite3.connect(stack.env["BANK_DB"])
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def reservation(stack, res_id):
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone())
    finally:
        conn.close()


def regular_plan():
    return [b for _, b in common.LEGIT_PLAN if b["beneficiary_id"].startswith("ben_reg")]


def trip_burst(stack, start: datetime) -> tuple[list, dict]:
    """8 executed payments with bases, then a 9th with its own basis trips burst_count_5m."""
    plan = regular_plan()
    for i, b in enumerate(plan[:8]):
        assert pay(stack, start + timedelta(seconds=i), b)["status"] == "executed"
    tripped = pay(stack, start + timedelta(seconds=8), plan[8])
    assert tripped["reason"] == "hard_stop" and tripped["rule_id"] == "burst_count_5m"
    return plan, tripped


def test_release_verdict_refuses_attack_without_basis(stack):
    replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    replaylib.replay(stack.breaker_url, replaylib.INCIDENTS_DIR / "inc_0001.jsonl")
    payments_before = bank(stack, "SELECT COUNT(*) FROM payments")[0][0]

    # The evidence names ids that look fine; the breaker ignores them and checks the database.
    r = verdict(stack, "inc_0001", body("release", evidence={"basis_ids": ["inv_3001"],
                                                             "prior_payment_ids": ["pay_000001"]},
                                        pr_url="https://example.invalid/pull/1"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["requested_verdict"] == "release"
    assert out["verdict"] == "contact_client"
    assert out["released"] == []
    assert len(out["still_held"]) == 36 and out["held_count"] == 36
    assert {x["why"] for x in out["still_held"]} == {"no approved unused basis with the same amount"}
    assert out["key_state"] == "paused" and out["status"] == "open"
    assert bank(stack, "SELECT COUNT(*) FROM payments")[0][0] == payments_before

    inc = stack.get("/state")["incidents"][0]
    assert inc["verdict"] == "contact_client" and inc["requested_verdict"] == "release"
    assert json.loads(inc["evidence_json"])["basis_ids"] == ["inv_3001"]
    assert inc["pr_url"] == "https://example.invalid/pull/1"
    assert inc["pause_to_pr_min"] is not None and inc["pause_to_pr_min"] >= 0
    texts = [e["text"] for e in stack.get("/state")["events"]]
    assert any("downgraded to contact_client" in t for t in texts)


def test_release_verdict_sends_only_payments_that_pass(stack):
    start = datetime.combine(common.LEGIT_DATE, datetime.min.time()).replace(hour=10)
    plan, tripped = trip_burst(stack, start)
    t = start + timedelta(seconds=9)
    dormant = pay(stack, t, {"account_id": common.ACCOUNT_ID, "beneficiary_id": "ben_dorm_01",
                             "amount_aed": 4500, "purpose": "no basis"})
    twice_a = pay(stack, t + timedelta(seconds=1), plan[9])
    twice_b = pay(stack, t + timedelta(seconds=2), plan[9])  # same basis as twice_a
    no_basis_id = {k: v for k, v in plan[10].items() if k != "basis_id"}
    lookup = pay(stack, t + timedelta(seconds=3), no_basis_id)
    new_supplier = next(b for _, b in common.LEGIT_PLAN if b["beneficiary_id"] == "ben_new_01")
    no_priors = pay(stack, t + timedelta(seconds=4), new_supplier)
    assert all(x["reason"] == "key_paused" for x in (dormant, twice_a, twice_b, lookup, no_priors))
    assert stack.get("/state")["open_incident"]["id"] == "inc_0001"

    r = verdict(stack, "inc_0001", body("release"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["released"] == [tripped["reservation_id"], twice_a["reservation_id"],
                               lookup["reservation_id"]]
    kept = {x["reservation_id"]: x["why"] for x in out["still_held"]}
    assert kept == {
        dormant["reservation_id"]: "beneficiary has no prior payments",
        twice_b["reservation_id"]: f"basis {plan[9]['basis_id']} is not approved, unused and of the same amount",
        no_priors["reservation_id"]: "beneficiary has no prior payments",
    }
    assert out["verdict"] == "contact_client" and out["key_state"] == "paused"

    looked_up = reservation(stack, lookup["reservation_id"])
    assert looked_up["status"] == "executed" and looked_up["decided_by"] == "verdict"
    assert looked_up["basis_id"] == plan[10]["basis_id"]
    assert bank(stack, "SELECT used FROM bases WHERE id = ?", plan[10]["basis_id"]) == [(1,)]
    assert bank(stack, "SELECT COUNT(*) FROM payments WHERE basis_id = ?",
                plan[9]["basis_id"]) == [(1,)]
    for res_id in kept:
        assert reservation(stack, res_id)["status"] == "held"


def test_release_verdict_all_pass_resumes_key(stack):
    start = datetime.combine(common.LEGIT_DATE, datetime.min.time()).replace(hour=10)
    plan, tripped = trip_burst(stack, start)
    r = verdict(stack, "inc_0001", body("release"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["released"] == [tripped["reservation_id"]] and out["still_held"] == []
    assert (out["verdict"], out["status"], out["key_state"]) == ("release", "closed", "normal")
    assert reservation(stack, tripped["reservation_id"])["status"] == "executed"
    assert stack.get("/state")["incidents"][0]["pause_to_pr_min"] is None  # no pr_url
    # The key works again, and a closed incident takes no more verdicts.
    later = start + timedelta(hours=2)
    assert pay(stack, later, plan[9])["status"] == "executed"
    assert verdict(stack, "inc_0001", body("contact_client")).status_code == 409


def test_verdict_validation_and_other_verdicts(stack, tmp_path):
    replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    replaylib.replay(stack.breaker_url, replaylib.INCIDENTS_DIR / "inc_0001.jsonl")

    assert verdict(stack, "inc_0001", body("contact_client"), headers={}).status_code == 401
    assert verdict(stack, "inc_0099", body("contact_client", "inc_0099")).status_code == 404
    assert verdict(stack, "inc_0001", body("contact_client", "inc_0002")).status_code == 422
    assert verdict(stack, "inc_0001", body("send_it_all")).status_code == 422
    assert verdict(stack, "inc_0001", {"incident_id": "inc_0001",
                                       "verdict": "release"}).status_code == 422
    assert verdict(stack, "inc_0001", body("release", note="extra field")).status_code == 422
    assert verdict(stack, "inc_0001", body("release", pr_url="not a url")).status_code == 422

    out = verdict(stack, "inc_0001", body("hold_until_morning")).json()
    assert out["hold_until"] == "2026-11-21 08:00:00" and out["held_count"] == 36

    vfile = tmp_path / "verdict.json"
    vfile.write_text(json.dumps(body("escalate_bank_security")))
    script = str(replaylib.ROOT / "scripts" / "apply_verdict.sh")
    r = subprocess.run(["bash", script, "inc_0001", str(vfile)], env=stack.env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["verdict"] == "escalate_bank_security"
    r = subprocess.run(["bash", script, "inc_0099", str(vfile)], env=stack.env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "HTTP 422" in r.stderr

    out = verdict(stack, "inc_0001", body("contact_client")).json()
    assert (out["verdict"], out["key_state"], out["held_count"]) == ("contact_client", "paused", 36)
    texts = [e["text"] for e in stack.get("/state")["events"]]
    assert any("bank security paged (simulated)" in t for t in texts)
    assert any("client contacted by second channel (simulated)" in t for t in texts)

    httpx.post(stack.breaker_url + "/admin/keys/key_microsoft_1/resume", headers=ADMIN, timeout=10)
    assert verdict(stack, "inc_0001", body("contact_client")).status_code == 409


def test_facts_held_reads_the_incident(stack):
    replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    replaylib.replay(stack.breaker_url, replaylib.INCIDENTS_DIR / "inc_0001.jsonl")
    script = str(replaylib.ROOT / "scripts" / "facts.sh")
    env = {**stack.env, "PYTHON": sys.executable}
    outputs = []
    for engine in ("python", "cli"):
        r = subprocess.run(["bash", script, "held", "inc_0001"], capture_output=True, text=True,
                           env={**env, "FACTS_ENGINE": engine}, timeout=60)
        if engine == "cli" and "sqlite3" in r.stderr and r.returncode != 0:
            continue  # no sqlite3 CLI on this machine
        assert r.returncode == 0, r.stderr
        outputs.append(r.stdout)
    assert len(set(outputs)) == 1
    assert outputs[0].count(" ben_dorm_0") == 36 + 3
    r = subprocess.run(["bash", script, "held", "inc_0001;--"], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 2
    r = subprocess.run(["bash", script, "nope"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "usage" in r.stderr
