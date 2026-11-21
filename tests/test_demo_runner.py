"""scripts/demo.py --auto runs all five cases against a stub stack and cleans up after itself."""

import json
import subprocess
import threading
import time

import httpx

from conftest import PYTHON, ROOT


def watch_presenter(url, seen, stop):
    """Record every distinct presenter state the runner sets, as the page would see it."""
    with httpx.Client(timeout=5) as c:
        while not stop.is_set():
            try:
                p = c.get(url + "/state").json()["presenter"]
            except (httpx.HTTPError, ValueError, KeyError):
                p = None
            if p and (not seen or seen[-1] != p):
                seen.append(p)
            time.sleep(0.05)


def test_demo_runner_auto_completes_all_cases(stack, tmp_path):
    seen, stop = [], threading.Event()
    t = threading.Thread(target=watch_presenter, args=(stack.breaker_url, seen, stop))
    t.start()
    try:
        r = subprocess.run(
            [PYTHON, str(ROOT / "scripts" / "demo.py"), "--auto", "--pace", "0.02",
             "--legit-delay", "0", "--attack-delay", "0.02", "--no-watcher",
             "--pr-state", str(tmp_path / "missing.json"), "--url", stack.breaker_url],
            cwd=ROOT, env=stack.env, capture_output=True, text=True, timeout=300)
    finally:
        stop.set()
        t.join()
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
    assert "Here: Al Noor Logistics LLC's payout key." in joined
    assert "42 of 42 executed. Zero held." in joined
    assert "Both are above 0.90, so the breaker releases the payment." in joined
    assert "AED 423,000 stopped. AED 27,000 leaked." in joined
    assert "No webhook configured, offline mode." in joined
    assert "Before the new rule: 6 of 42 attack payments were executed." in joined
    assert "The fix is not available yet" in joined
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
