import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import common
import replaylib

INCIDENT_TRACE = replaylib.INCIDENTS_DIR / "inc_0001.jsonl"


class Receiver:
    """A local webhook endpoint that records every POST and answers with `status`."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                receiver.calls.append((dict(self.headers), raw))
                self.send_response(receiver.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def wait_for(fn, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


def events(stack, kind_text):
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    try:
        return [r[0] for r in conn.execute("SELECT text FROM events WHERE text LIKE ?",
                                           (f"%{kind_text}%",))]
    finally:
        conn.close()


def run_demo(stack):
    legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    attack = replaylib.replay(stack.breaker_url, INCIDENT_TRACE)
    assert (legit.executed, legit.held) == (42, 0)
    assert (attack.executed, attack.held) == (6, 36)


def test_webhook_body(make_stack, receiver):
    stack = make_stack(env={"DEVIN_WEBHOOK_URL": receiver.url,
                            "DEVIN_WEBHOOK_SECRET": "test-webhook-secret"})
    run_demo(stack)
    assert wait_for(lambda: receiver.calls)
    time.sleep(0.3)
    assert len(receiver.calls) == 1, "one webhook per incident"
    headers, raw = receiver.calls[0]
    assert headers["X-Webhook-Secret"] == "test-webhook-secret"
    assert common.API_KEY_SECRET.encode() not in raw
    body = json.loads(raw)

    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    asked, released = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(released), 0) FROM jev_calls").fetchone()
    conn.close()
    expected_samples = [json.loads(x) for x in INCIDENT_TRACE.read_text().splitlines()[:9]]
    samples = body.pop("sample_requests")
    assert len(samples) == len(expected_samples)
    conn = sqlite3.connect(stack.env["BREAKER_DB"])
    known_ids = {r[0] for r in conn.execute("SELECT id FROM reservations")}
    conn.close()
    # Reservation ids depend on what ran before (the fixture was recorded in demo order).
    for got, want in zip(samples, expected_samples):
        assert got.pop("reservation_id") in known_ids
        want.pop("reservation_id", None)
        assert got == want
    assert body == {
        "event": "breaker.tripped",
        "incident_id": "inc_0001",
        "tripped_at": "2026-11-21T02:03:04+04:00",
        "rule": "burst_count_5m",
        "level": "hard",
        "api_key_id": "key_microsoft_1",
        "endpoint": "POST /payouts",
        "window": {"seconds": 300, "count": 9, "sum_aed": 62250, "new_beneficiaries": 3},
        "held_count": 3,
        "leaked_count": 6,
        "jev": {"model": "jev-1.13.0", "asked": asked, "released": released},
        "trace_file": str(stack.workdir / "incidents" / "inc_0001.jsonl"),
    }
    assert wait_for(lambda: events(stack, "webhook for inc_0001 sent"))


@pytest.mark.parametrize("failure", ["http_500", "unreachable"])
def test_failing_webhook_does_not_affect_hold(make_stack, failure):
    receiver = Receiver(status=500)
    try:
        url = receiver.url if failure == "http_500" else f"http://127.0.0.1:{replaylib.free_port()}/"
        stack = make_stack(env={"DEVIN_WEBHOOK_URL": url})
        run_demo(stack)
        errors = wait_for(lambda: events(stack, "webhook for inc_0001 failed"))
        assert len(errors) == 1
        state = stack.get("/state")
        assert state["keys"][0]["state"] == "paused"
        assert state["reservations"]["held"] == 36
        assert state["open_incident"]["id"] == "inc_0001"
    finally:
        receiver.close()


def test_no_webhook_without_url(stack, receiver):
    run_demo(stack)
    time.sleep(0.5)
    assert receiver.calls == []
    assert events(stack, "webhook") == []
