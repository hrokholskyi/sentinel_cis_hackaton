"""Helpers for the security hardening tests: fake Jev, a bank proxy, an extra breaker."""

import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

import common
from conftest import PYTHON, ROOT, free_port, wait_healthy

if str(ROOT) not in sys.path:  # so tests can import breaker modules in-process
    sys.path.insert(0, str(ROOT))

ADMIN = {"X-Admin-Token": "dev-admin-token"}
SOFT_AT = datetime.combine(common.DEMO_DATE, datetime.strptime("02:10:00", "%H:%M:%S").time())
SOFT = {"account_id": common.ACCOUNT_ID, "beneficiary_id": common.SOFT_BENEFICIARY_ID,
        "amount_aed": common.SOFT_AMOUNT_AED, "purpose": common.SOFT_PURPOSE,
        "basis_id": common.SOFT_BASIS_ID}
# One payment above 30% of the balance: trips the hard rules drain_1h and burst_sum_1h.
DRAIN = {"account_id": common.ACCOUNT_ID, "beneficiary_id": "ben_reg_01",
         "amount_aed": 460000, "purpose": "Freight, annual prepayment"}


def sim(ts: datetime, token: str = None) -> dict:
    """X-Sim-Time with a valid X-Sim-Token (or the given token)."""
    return {"X-Sim-Time": common.iso(ts), "X-Sim-Token": common.SIM_TOKEN if token is None
            else token}


def pay(url: str, ts: datetime, body: dict, token: str = None) -> httpx.Response:
    return httpx.post(url + "/payouts", json=body, timeout=30,
                      headers={"X-API-Key": common.API_KEY_SECRET, **sim(ts, token)})


def q(db: str, sql: str, *args) -> list:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def wait_for(pred, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


class FakeJev:
    """A fake TypeSafe endpoint: fixed scores after a delay. Counts the POSTs it received."""

    def __init__(self, score: float = 0.99, delay: float = 0.0):
        self.score, self.delay, self.posts = score, delay, 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # warm-up
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                fake.posts += 1
                time.sleep(fake.delay)
                body = json.dumps({"answers": {q: {"type": "noul", "noul": fake.score}
                                               for q in ("fits_history", "purpose_matches_basis")}})
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body.encode())
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def env(self) -> dict:
        return {"JEV_MODE": "live", "TYPESAFE_API_KEY": "dummy-key", "JEV_TIMEOUT_S": "10",
                "JEV_URL": f"http://127.0.0.1:{self.server.server_port}/v1/systemone"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class BankProxy:
    """Forwards POST /payouts to the bank. drop: lose the answer of the next N calls.
    delay: seconds to wait before forwarding."""

    def __init__(self, drop: int = 0, delay: float = 0.0):
        self.target, self.drop, self.delay, self.calls = None, drop, delay, 0
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                proxy.calls += 1
                time.sleep(proxy.delay)
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "content-length", "connection")}
                r = httpx.post(proxy.target + self.path, content=raw, headers=headers, timeout=30)
                if proxy.drop > 0:
                    proxy.drop -= 1
                    self.close_connection = True
                    return  # the bank executed it, the answer is lost
                self.send_response(r.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(r.content)))
                self.end_headers()
                self.wfile.write(r.content)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def extra_breaker(stack, **env):
    """A second breaker on the stack's databases with extra environment, stopped at the end."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    full = dict(stack.env, **env)
    log = open(stack.tmp / f"breaker_{port}.log", "w")
    proc = subprocess.Popen([PYTHON, "-m", "uvicorn", "breaker.app:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=full, stdout=log,
                            stderr=subprocess.STDOUT)
    try:
        wait_healthy(url, proc)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
