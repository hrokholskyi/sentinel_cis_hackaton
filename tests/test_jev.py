import json
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from conftest import PYTHON, ROOT, free_port, wait_healthy


def run_legit(stack):
    r = stack.run_script("traffic_legit.py", "--delay", "0", "--trace", str(stack.tmp / "legit.jsonl"))
    assert r.returncode == 0, r.stdout + r.stderr


def jev_rows(stack):
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM jev_calls ORDER BY id").fetchall()
    conn.close()
    return rows


def test_stub_jev_releases_soft_case(stack):
    run_legit(stack)
    r = stack.run_script("traffic_soft.py", "--delay", "0", "--trace", str(stack.tmp / "soft.jsonl"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sent 1 executed 1 held 0" in r.stdout

    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    res = conn.execute("SELECT status, decided_by, rule_id, payment_id FROM reservations"
                       " WHERE amount_aed = 48000").fetchone()
    conn.close()
    assert res[0] == "executed" and res[1] == "jev" and res[3]

    rows = jev_rows(stack)
    assert len(rows) == 1 and rows[0]["released"] == 1 and rows[0]["mode"] == "stub"
    state = json.loads(rows[0]["request_json"])["state"]
    assert state["beneficiary"]["prior_payments"] == 14
    assert state["beneficiary"]["max_amount_aed"] == 52000
    assert state["beneficiary"]["min_amount_aed"] == 30000
    assert state["beneficiary"]["prior_payments_last_24h"] == 0
    assert state["beneficiary"]["share_with_approved_basis"] == 1
    assert state["basis"] == {"type": "invoice", "id": "inv_4471", "amount_aed": 48000, "approved": True,
                              "already_used": False,
                              "description": "Invoice 4471 from Falcon Cargo Handling: "
                                             "logistics services, October 2026"}
    assert any(e["kind"] == "jev" and "released" in e["text"] for e in stack.get("/state")["events"])

    # Demo order legit, soft, attack: the attack still leaks exactly 6 and Jev keeps 7 and 8.
    a = stack.run_script("traffic_attack.py", "--delay", "0", "--trace", str(stack.tmp / "attack.jsonl"))
    assert a.returncode == 0, a.stdout + a.stderr
    assert "sent 42 executed 6 held 36" in a.stdout
    rows = jev_rows(stack)
    assert [r["released"] for r in rows] == [1, 0, 0]
    attack = json.loads(rows[1]["request_json"])["state"]["beneficiary"]
    assert attack["prior_payments"] == 2 and attack["prior_payments_last_24h"] == 2


class SlowJev(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(3)
        try:
            body = json.dumps({"answers": {"fits_history": {"type": "noul", "noul": 0.99},
                                           "purpose_matches_basis": {"type": "noul", "noul": 0.99}}})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())
        except OSError:
            pass

    def log_message(self, *args):
        pass


def test_jev_timeout_keeps_hold(stack):
    run_legit(stack)
    server = HTTPServer(("127.0.0.1", 0), SlowJev)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = free_port()
    env = dict(stack.env, JEV_MODE="live", TYPESAFE_API_KEY="dummy-key",
               JEV_URL=f"http://127.0.0.1:{server.server_port}/v1/systemone")
    breaker_url = f"http://127.0.0.1:{port}"
    log = open(stack.tmp / "breaker_live.log", "w")
    proc = subprocess.Popen([PYTHON, "-m", "uvicorn", "breaker.app:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    try:
        wait_healthy(breaker_url, proc)
        t0 = time.perf_counter()
        r = subprocess.run([PYTHON, str(ROOT / "scripts" / "traffic_soft.py"), "--delay", "0",
                            "--url", breaker_url, "--expect", "held",
                            "--trace", str(stack.tmp / "soft.jsonl")],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        elapsed = time.perf_counter() - t0
        assert r.returncode == 0, r.stdout + r.stderr
        assert "executed 0 held 1" in r.stdout
        assert elapsed < 2.5, elapsed
        rows = jev_rows(stack)
        assert len(rows) == 1 and rows[0]["released"] == 0 and rows[0]["mode"] == "live"
        assert rows[0]["response_json"] is None
        assert "dummy-key" not in rows[0]["request_json"]
        state = httpx.get(breaker_url + "/state", timeout=5).json()
        assert state["reservations"]["held"] == 1
        # The start-up warm-up GET hit the fake server (no GET handler): only an event.
        assert any(e["kind"] == "jev" and "warm-up failed" in e["text"] for e in state["events"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
        server.shutdown()
        server.server_close()
