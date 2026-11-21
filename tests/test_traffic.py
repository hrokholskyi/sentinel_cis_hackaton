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
