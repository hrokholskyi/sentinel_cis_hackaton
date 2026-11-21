"""Rules are read-only (security hardening, finding 5)."""

import sqlite3

import pytest

import hardening_helpers  # noqa: F401  (puts the repo root on sys.path)
import replaylib
from breaker import engine
from conftest import LIVE_RULES, baseline_rules_dir

HEADER = "-- id: sneaky\n-- level: soft\n-- description: writes\n"
WRITERS = {
    "update": "UPDATE key_state SET state = 'normal' RETURNING 0 AS violated, 'ok' AS detail;",
    "with_update": "WITH k AS (SELECT 1) UPDATE key_state SET state = 'normal'"
                   " RETURNING 0 AS violated, 'ok' AS detail;",
    "two_statements": "SELECT 0 AS violated, 'ok' AS detail; DELETE FROM key_state;",
    "pragma": "PRAGMA writable_schema = 1;",
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_load_refuses_a_rule_that_is_not_one_select(tmp_path, name):
    (tmp_path / "01_sneaky.sql").write_text(HEADER + WRITERS[name] + "\n")
    with pytest.raises(ValueError, match="single read-only SELECT"):
        engine.load_rules(str(tmp_path))


def test_load_accepts_select_and_with_select(tmp_path):
    (tmp_path / "01_a.sql").write_text(
        "-- id: a\n-- level: hard\n-- note: a ; in a comment and 'UPDATE' in a string\n"
        "SELECT 0 AS violated, 'x; UPDATE y' AS detail;\n")
    (tmp_path / "02_b.sql").write_text(
        "-- id: b\n-- level: soft\n/* block; comment */\n"
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3)\n"
        "SELECT COUNT(*) > 5 AS violated, 'n ' || COUNT(*) AS detail FROM n\n")
    assert [r.id for r in engine.load_rules(str(tmp_path))] == ["a", "b"]
    assert len(engine.load_rules(str(LIVE_RULES))) >= 6


def test_breaker_does_not_start_with_a_writing_rule(tmp_path):
    rules = baseline_rules_dir(tmp_path / "rules")
    (rules / "07_reset_key.sql").write_text(HEADER + WRITERS["update"] + "\n")
    with pytest.raises(RuntimeError, match="exited early"):
        with replaylib.start(tmp_path / "stack", rules_dir=rules):
            pass
    log = (tmp_path / "stack" / "breaker.log").read_text()
    assert "07_reset_key.sql" in log and "single read-only SELECT" in log


def test_evaluate_denies_writes_at_run_time():
    """Defence in depth: even a rule that bypassed the loader cannot write."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE key_state (api_key_id TEXT, state TEXT)")
    conn.execute("INSERT INTO key_state VALUES ('k', 'paused')")
    writer = engine.Rule("sneaky", "soft", "", WRITERS["update"], "x.sql")
    reader = engine.Rule("reader", "hard", "", "SELECT COUNT(*) > 5 AS violated,"
                         " 'n ' || COUNT(*) AS detail FROM key_state WHERE api_key_id = :k", "y.sql")
    out = engine.evaluate(conn, [writer, reader], {"k": "k"})
    assert out[0].violated and out[0].error and "rule error" in out[0].detail
    assert not out[1].violated and out[1].detail == "n 1" and not out[1].error
    assert conn.execute("SELECT state FROM key_state").fetchone()[0] == "paused"
    conn.execute("UPDATE key_state SET state = 'probe'")  # the authorizer is removed again
    conn.close()
