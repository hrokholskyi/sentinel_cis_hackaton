import os
import subprocess
import sys

import replaylib


def test_replay_fails_on_current_rules():
    """Until a rule for this pattern exists, 6 attack payments leak and replay.sh exits 1."""
    env = {**os.environ, "PYTHON": sys.executable}
    r = subprocess.run(["bash", str(replaylib.ROOT / "scripts" / "replay.sh")], env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 1, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert lines[-3:] == [
        "                      sent   executed   held",
        "attack                 42        6       36     FAIL (executed must be 0)",
        "legit                  42       42        0     PASS (executed must be 100%)",
    ]
