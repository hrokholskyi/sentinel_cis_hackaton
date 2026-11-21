import os
import re
import subprocess
import sys

import replaylib
from conftest import LIVE_RULES, baseline_rules_dir


def test_baseline_replay_leaks_six(tmp_path):
    """On the six original rules, 6 attack payments leak. This is the pinned incident."""
    attack, legit = replaylib.run(replaylib.INCIDENTS_DIR / "inc_0001.jsonl", tmp_path / "run",
                                  rules_dir=baseline_rules_dir(tmp_path / "rules"))
    assert replaylib.table(attack, legit).splitlines() == [
        "                      sent   executed   held",
        "attack                 42        6       36     FAIL (executed must be 0)",
        "legit                  42       42        0     PASS (executed must be 100%)",
    ]


def test_replay_cli_on_live_rules():
    """Only what stays true after a rule closes the gap."""
    env = {**os.environ, "PYTHON": sys.executable}
    r = subprocess.run(["bash", str(replaylib.ROOT / "scripts" / "replay.sh")], env=env,
                       capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert lines[-4] == ("replay of fixtures/incidents/inc_0001.jsonl after fixtures/legit.jsonl"
                         " (fresh seed, JEV_MODE=stub, no webhook)"), out
    assert lines[-3] == "                      sent   executed   held", out
    attack = re.fullmatch(r"attack\s+(\d+)\s+(\d+)\s+(\d+)\s+(PASS|FAIL) \(executed must be 0\)",
                          lines[-2])
    assert attack, out
    sent, executed, held, verdict = attack.groups()
    assert int(sent) == 42 and int(executed) + int(held) == 42, out
    assert re.fullmatch(r"legit\s+42\s+42\s+0\s+PASS \(executed must be 100%\)", lines[-1]), out
    assert r.returncode == (0 if verdict == "PASS" else 1), out


def test_every_live_rule_loads(make_stack):
    """The breaker starts (make_stack waits for /health) with every file in rules/."""
    stack = make_stack(rules_dir=LIVE_RULES)
    assert stack.get("/health")


def test_live_rules_do_not_hold_legit_traffic(make_stack):
    stack = make_stack(rules_dir=LIVE_RULES)
    legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
    assert (legit.sent, legit.executed, legit.held) == (42, 42, 0)
