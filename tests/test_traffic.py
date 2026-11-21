import json
import sqlite3


def test_legit_traffic_all_executed(stack):
    trace = stack.tmp / "legit.jsonl"
    r = stack.run_script("traffic_legit.py", "--delay", "0", "--trace", str(trace))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sent 42 executed 42 held 0" in r.stdout
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert len(lines) == 42
    assert all(x["status"] == "executed" and x["reservation_id"] for x in lines)
    assert all("X-API-Key" not in json.dumps(x) for x in lines)

    conn = sqlite3.connect(stack.env["BANK_DB"])
    assert conn.execute("SELECT COUNT(*) FROM payments WHERE created_at LIKE '2026-11-20%'"
                        ).fetchone()[0] == 42
    conn.close()
    state = stack.get("/state")
    assert state["reservations"]["executed"] == 42
    assert state["reservations"]["held"] == 0
    assert state["keys"][0]["state"] == "normal"
    assert state["open_incident"] is None


def test_attack_pauses_key(stack):
    legit = stack.run_script("traffic_legit.py", "--delay", "0", "--trace",
                             str(stack.tmp / "legit.jsonl"))
    assert legit.returncode == 0, legit.stdout + legit.stderr

    r = stack.run_script("traffic_attack.py", "--delay", "0", "--trace",
                         str(stack.tmp / "attack.jsonl"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sent 42 executed 6 held 36" in r.stdout

    state = stack.get("/state")
    assert state["keys"][0]["state"] == "paused"
    assert state["reservations"]["executed"] == 42 + 6
    assert state["reservations"]["held"] == 36
    inc = state["open_incident"]
    assert inc["id"] == "inc_0001"
    assert inc["rule_id"] == "burst_count_5m"
    assert inc["tripped_at"] == "2026-11-21 02:03:04"
    assert inc["first_violation_at"] == "2026-11-21 02:03:00"
    assert 4000 <= inc["violation_to_pause_ms"] < 5000

    lines = [json.loads(x) for x in (stack.tmp / "incidents" / "inc_0001.jsonl")
             .read_text().splitlines()]
    assert len(lines) == 42
    assert [x["status"] for x in lines] == ["executed"] * 6 + ["held"] * 36
    attack = [json.loads(x) for x in (stack.tmp / "attack.jsonl").read_text().splitlines()]
    assert [x["request"] for x in lines] == [x["request"] for x in attack]
