"""Shared harness: seed temp databases and run the real bank and breaker as subprocesses.

The harness lives in scripts/replaylib.py, shared with replay.sh and the snapshot builder.

The default fixtures run on a pinned baseline of the six original rules (see
BASELINE_RULE_FILES), so adding a rule file to rules/ does not change tests about the
historic incident. A test of the live folder passes rules_dir=LIVE_RULES to make_stack.
"""

import shutil
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import replaylib  # noqa: E402
from replaylib import PYTHON, free_port, wait_healthy  # noqa: E402,F401  (used by test modules)

LIVE_RULES = ROOT / "rules"

BASELINE_RULE_FILES = (
    "01_off_hours_amount.sql",
    "02_amount_above_typical.sql",
    "03_first_payment_above_limit.sql",
    "04_burst_count_5m.sql",
    "05_burst_sum_1h.sql",
    "06_drain_1h.sql",
)


def baseline_rules_dir(dest: Path) -> Path:
    """Copy exactly the baseline rule files from rules/ into dest and return dest."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in BASELINE_RULE_FILES:
        shutil.copy(LIVE_RULES / name, dest / name)
    return dest


@pytest.fixture
def stack(tmp_path):
    """Fresh seed, bank and breaker on free ports, stub Jev, no webhook, baseline rules."""
    with replaylib.start(tmp_path, rules_dir=baseline_rules_dir(tmp_path / "baseline_rules")) as s:
        yield s


@pytest.fixture
def make_stack(tmp_path):
    """Factory for a stack: make_stack(env={...}, rules_dir=None).

    Baseline rules unless rules_dir is given, for example rules_dir=LIVE_RULES.
    """
    with ExitStack() as stacks:
        n = 0

        def make(env=None, rules_dir=None):
            nonlocal n
            n += 1
            if rules_dir is None:
                rules_dir = baseline_rules_dir(tmp_path / f"baseline_rules{n}")
            return stacks.enter_context(replaylib.start(tmp_path / f"stack{n}", env=env,
                                                        rules_dir=rules_dir))
        yield make
