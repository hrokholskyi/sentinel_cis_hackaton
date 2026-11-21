import sqlite3

import replaylib  # tests/conftest.py puts scripts/ on sys.path

RULE_ID = "dormant_beneficiary_no_basis"


def test_inc_0001_is_fully_held(tmp_path):
    with replaylib.start(tmp_path) as stack:  # fresh seed, bank and breaker on free ports
        legit = replaylib.replay(stack.breaker_url, replaylib.LEGIT_TRACE)
        attack = replaylib.replay(stack.breaker_url,
                                  replaylib.INCIDENTS_DIR / "inc_0001.jsonl")
        db = sqlite3.connect(stack.env["BREAKER_DB"])
        try:
            legit_ids = [line["reservation_id"] for line in legit.lines]
            held_by_rule = db.execute(
                f"SELECT COUNT(*) FROM reservations WHERE rule_id = ? AND id IN"
                f" ({','.join('?' * len(legit_ids))})", (RULE_ID, *legit_ids)).fetchone()[0]
            first = db.execute("SELECT rule_id, level, status FROM reservations WHERE id = ?",
                               (attack.lines[0]["reservation_id"],)).fetchone()
        finally:
            db.close()
    assert attack.sent == 42
    assert attack.executed == 0
    assert attack.held == attack.sent
    assert legit.executed == legit.sent
    assert held_by_rule == 0
    # The first warm-up payment (AED 4,500 to a dormant beneficiary at 02:00) trips the new rule.
    assert tuple(first) == (RULE_ID, "hard", "held")
