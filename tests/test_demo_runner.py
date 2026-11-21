"""scripts/demo.py --auto runs all five cases against a stub stack and cleans up after itself."""

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from conftest import PYTHON, ROOT, baseline_rules_dir

JEV_APPROVED = "The burst starts. Soft stop. Jev says this does not fit the history, " \
               "so the payments stay on hold."


class LowScoreJev(BaseHTTPRequestHandler):
    """A fake TypeSafe endpoint: low scores, after a delay (the live answer takes ~250 ms)."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(0.4)
        body = json.dumps({"answers": {"fits_history": {"type": "noul", "noul": 0.02},
                                       "purpose_matches_basis": {"type": "noul", "noul": 0.01}}})
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())
        except OSError:
            pass

    def log_message(self, *args):
        pass


FIELDS = ("caption", "spotlight", "card", "step", "step_done", "panel")


def presenter_states(log_path):
    """Every presenter state the runner set, in order, rebuilt from its --presenter-log.

    The breaker accepted each update (the runner stops on any other answer) and merges it
    like this: a given field replaces the old value, a missing field keeps it. Reading the
    log instead of polling /state never misses a caption that was up only briefly.
    """
    cur, seen = dict.fromkeys(FIELDS), []
    for line in log_path.read_text().splitlines():
        upd = json.loads(line)
        if upd.get("_clear"):
            cur = dict.fromkeys(FIELDS)
        else:
            cur.update({k: v for k, v in upd.items() if k in FIELDS})
        seen.append(dict(cur, step_done=cur["step_done"] or []))
    return seen


def run_runner(stack, tmp_path, *extra):
    log = tmp_path / "presenter.jsonl"
    log.unlink(missing_ok=True)
    r = subprocess.run(
        [PYTHON, str(ROOT / "scripts" / "demo.py"), "--auto", "--pace", "0.02",
         "--legit-delay", "0", "--attack-delay", "0.02", "--no-watcher",
         "--before-rules", str(baseline_rules_dir(tmp_path / "before_rules")),
         "--pr-state", str(tmp_path / "missing.json"), "--url", stack.breaker_url,
         "--presenter-log", str(log), *extra],
        cwd=ROOT, env=stack.env, capture_output=True, text=True, timeout=300)
    return r, presenter_states(log)


def test_case3_caption_waits_for_slow_jev(make_stack, tmp_path):
    """The hold is committed before Jev is called: the caption must not say Jev was not asked."""
    server = HTTPServer(("127.0.0.1", 0), LowScoreJev)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        stack = make_stack(env={"JEV_MODE": "live", "TYPESAFE_API_KEY": "dummy-key",
                                "JEV_URL": f"http://127.0.0.1:{server.server_port}/v1/systemone"})
        r, seen = run_runner(stack, tmp_path, "--no-tour")
        assert r.returncode == 0, r.stdout + r.stderr
        captions = [p["caption"] for p in seen if p["caption"]]
        assert JEV_APPROVED in captions
        assert not any("Jev was not asked" in c or "Jev gave no answer" in c for c in captions)
    finally:
        server.shutdown()
        server.server_close()
        for name in ("legit", "soft", "attack"):
            (ROOT / "traces" / f"demo_{name}.jsonl").unlink(missing_ok=True)


def test_demo_runner_auto_completes_all_cases(stack, tmp_path):
    r, seen = run_runner(stack, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    for case in range(1, 6):
        assert f"Case {case}:" in out

    s = stack.get("/state")
    assert s["presenter"]["active"] is False
    # The runner really ran legit, soft and attack: the demo numbers are on the page.
    assert s["counters"]["stopped"] == {"count": 36, "aed": 423000}
    assert s["counters"]["leaked"] == {"count": 6, "aed": 27000}
    assert s["counters"]["released_by_jev"]["count"] == 1
    assert s["key"]["state"] == "paused"

    captions = [p["caption"] for p in seen if p["caption"]]
    joined = "\n".join(captions)
    assert "in front of the ADCB payments API" in joined
    assert "Here: Microsoft's payout key." in joined
    assert "42 of 42 executed. Zero held." in joined
    assert "Both scores are above 0.90, so the breaker releases the payment." in joined
    assert "AED 423,000 stopped. But AED 27,000 leaked." in joined
    assert "Offline mode: no webhook is configured, so Devin is not called." in joined
    assert "Before the new rule: 6 of 42 attack payments executed." in joined
    assert "The fix is not available yet" in joined
    # The stretch while the rest of the attack streams in has its own caption.
    assert "Each further payment on the paused API key is held. Offline mode: no webhook to Devin." in joined
    # Offline: no caption claims that Devin is working.
    assert "Devin is working" not in joined
    assert "No pull request yet: offline mode." in joined
    assert "The demo is finished. The services keep running" in out
    assert all("\u2014" not in c for c in captions)
    assert any(p["card"] == {"kicker": "Case 3", "title": "Attack"} for p in seen)
    assert any(p["panel"] and "FAIL" in p["panel"]["text"] for p in seen)
    assert {p["spotlight"] for p in seen} >= {"all", "layers", "key", "counters", "stream",
                                              "queue", "incident", "layer1", "layer2", "layer3"}
    assert max(p["step"] for p in seen if p["step"] is not None) == 5
    assert any(p["step_done"] == [0, 1, 2, 3, 4] for p in seen)  # case 5 is marked just before the clear
    # The runner's traces go to traces/ (gitignored), never to the committed fixtures.
    for name in ("legit", "soft", "attack"):
        trace = ROOT / "traces" / f"demo_{name}.jsonl"
        assert json.loads(trace.read_text().splitlines()[0])
        trace.unlink()
