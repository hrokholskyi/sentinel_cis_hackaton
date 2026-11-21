"""Milestone 6 acceptance: the payroll burst is held by a hard stop, a release verdict sends
all 30 payments."""

import json
import sqlite3
import subprocess

import common
import replaylib

VERDICT_FILE = replaylib.ROOT / "fixtures" / "verdicts" / "payroll_release.json"


def rows(path, sql, *args):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_payroll_plan_matches_the_seeded_payroll():
    plan = common.payroll_plan({b: 1000 for b in common.EMPLOYEE_IDS})
    assert len(plan) == 30
    assert plan[0][0].isoformat() == "2026-11-25T09:00:00"
    assert plan[-1][0].isoformat() == "2026-11-25T09:00:58"
    assert [b["beneficiary_id"] for _, b in plan] == common.EMPLOYEE_IDS
    assert plan[8][1]["basis_id"] == "pr_2026_11_ben_emp_09"
    v = json.loads(VERDICT_FILE.read_text())
    assert v["incident_id"] == "inc_0001" and v["verdict"] == "release"
    assert v["evidence"]["basis_ids"] == [b["basis_id"] for _, b in plan[8:]]


def test_payroll_burst_is_held_then_released_by_verdict(stack):
    r = stack.run_script("traffic_payroll.py", "--delay", "0", "--trace",
                         str(stack.tmp / "payroll.jsonl"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sent 30 executed 8 held 22" in r.stdout
    assert "incident inc_0001 open" in r.stdout

    s = stack.get("/state")
    assert s["key"]["state"] == "paused"
    inc = s["open_incident"]
    assert (inc["id"], inc["rule_id"], inc["tripped_at"]) == \
        ("inc_0001", "burst_count_5m", "2026-11-25 09:00:16")
    # Every held payroll payment would pass the release re-check.
    assert s["counters"]["legit_held"]["count"] == 22
    assert s["counters"]["stopped"]["count"] == 22
    assert s["counters"]["leaked"]["count"] == 8

    script = str(replaylib.ROOT / "scripts" / "apply_verdict.sh")
    r = subprocess.run(["bash", script, "inc_0001", str(VERDICT_FILE)], env=stack.env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert (out["verdict"], out["status"], out["key_state"]) == ("release", "closed", "normal")
    assert len(out["released"]) == 22 and out["still_held"] == [] and out["held_count"] == 0

    breaker_db, bank_db = stack.env["BREAKER_DB"], stack.env["BANK_DB"]
    assert rows(breaker_db, "SELECT status, COUNT(*) FROM reservations GROUP BY status") == \
        [("executed", 30)]
    assert rows(breaker_db, "SELECT decided_by, COUNT(*) FROM reservations GROUP BY decided_by"
                " ORDER BY 1") == [("breaker", 8), ("verdict", 22)]
    assert rows(bank_db, "SELECT COUNT(*) FROM payments WHERE beneficiary_id LIKE 'ben_emp_%'"
                " AND created_at LIKE '2026-11-25%'") == [(30,)]
    assert rows(bank_db, "SELECT COUNT(*) FROM bases WHERE id LIKE 'pr_2026_11_%' AND used = 1"
                ) == [(30,)]

    s = stack.get("/state")
    assert s["key"]["state"] == "normal" and s["open_incident"] is None
    assert s["incidents"][0]["status"] == "closed"
    # A fully released incident was legitimate: nothing stopped, nothing leaked.
    assert s["counters"]["stopped"]["count"] == 0
    assert s["counters"]["leaked"] == {"count": 0, "aed": 0}
    assert s["counters"]["legit_held"]["count"] == 0
