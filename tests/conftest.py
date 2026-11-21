"""Shared harness: seed temp databases and run the real bank and breaker as subprocesses.

The harness lives in scripts/replaylib.py, shared with replay.sh and the snapshot builder.
"""

import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import replaylib  # noqa: E402
from replaylib import PYTHON, free_port, wait_healthy  # noqa: E402,F401  (used by test modules)


@pytest.fixture
def stack(tmp_path):
    """Fresh seed, bank and breaker on free ports, stub Jev, no webhook."""
    with replaylib.start(tmp_path) as s:
        yield s


@pytest.fixture
def make_stack(tmp_path):
    """Factory for a stack with extra environment variables: make_stack(env={...})."""
    with ExitStack() as stacks:
        n = 0

        def make(env=None, rules_dir=None):
            nonlocal n
            n += 1
            return stacks.enter_context(replaylib.start(tmp_path / f"stack{n}", env=env,
                                                        rules_dir=rules_dir))
        yield make
