"""Layer 1: rule loader and evaluator, reservations (counters) and the key state machine.

Rules are files in RULES_DIR, one SELECT each, returning one row (violated, detail).
They run on the breaker connection, with bank.db attached read-only as schema `bank`.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HEADER = re.compile(r"^--\s*(id|level|description|params):\s*(.*?)\s*$")
LEVELS = ("soft", "hard")
COUNTED = ("pending", "executed", "held", "released")


@dataclass(frozen=True)
class Rule:
    id: str
    level: str
    description: str
    sql: str
    path: str


@dataclass(frozen=True)
class Result:
    rule_id: str
    level: str
    violated: bool
    detail: str


def load_rules(rules_dir: str) -> list[Rule]:
    """Load rules/*.sql in file-name order. A malformed header stops the breaker from starting."""
    rules = []
    seen = set()
    for path in sorted(Path(rules_dir).glob("*.sql")):
        text = path.read_text()
        meta = {}
        for line in text.splitlines():
            m = HEADER.match(line)
            if m:
                meta.setdefault(m.group(1), m.group(2))
        if not meta.get("id") or meta.get("level") not in LEVELS:
            raise ValueError(f"{path}: needs '-- id:' and '-- level: soft|hard' headers")
        if meta["id"] in seen:
            raise ValueError(f"{path}: duplicate rule id {meta['id']}")
        seen.add(meta["id"])
        rules.append(Rule(meta["id"], meta["level"], meta.get("description", ""), text, str(path)))
    if not rules:
        raise ValueError(f"no rules found in {rules_dir}")
    return rules


def evaluate(conn: sqlite3.Connection, rules: list[Rule], params: dict) -> list[Result]:
    """Run every rule. A rule that errors or returns a malformed row counts as violated."""
    results = []
    for rule in rules:
        try:
            rows = conn.execute(rule.sql, params).fetchall()
            if len(rows) != 1:
                raise ValueError(f"expected 1 row, got {len(rows)}")
            row = rows[0]
            violated, detail = row["violated"], row["detail"]
            if violated is None:
                raise ValueError("violated is NULL")
            results.append(Result(rule.id, rule.level, bool(violated), str(detail)))
        except Exception as e:  # fail closed
            results.append(Result(rule.id, rule.level, True, f"rule error: {e}"))
    return results


def pick(violations: list[Result]) -> Optional[Result]:
    """The violation stored on the reservation: first hard rule if any, else first soft rule."""
    for v in violations:
        if v.level == "hard":
            return v
    return violations[0] if violations else None


# --- reservations -----------------------------------------------------------

def insert_reservation(conn, *, api_key_id, body, status, now, rule_id=None, level=None,
                       detail=None) -> str:
    """Insert a reservation and return its id. Call inside BEGIN IMMEDIATE."""
    n = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] + 1
    res_id = f"res_{n:06d}"
    conn.execute(
        "INSERT INTO reservations (id, api_key_id, account_id, beneficiary_id, amount_aed, purpose,"
        " basis_id, status, rule_id, level, detail, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (res_id, api_key_id, body["account_id"], body["beneficiary_id"], body["amount_aed"],
         body.get("purpose", ""), body.get("basis_id"), status, rule_id, level, detail, now),
    )
    return res_id


def set_status(conn, res_id: str, status: str, now: str, decided_by: str, **extra) -> None:
    cols = {"status": status, "decided_at": now, "decided_by": decided_by, **extra}
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE reservations SET {sets} WHERE id = ?", (*cols.values(), res_id))


def get_reservation(conn, res_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()


# --- key state machine: normal -> paused -> probe -> normal ------------------

def key_state(conn, api_key_id: str, now: str) -> sqlite3.Row:
    """Current state of a key; a key seen for the first time starts as normal."""
    row = conn.execute("SELECT * FROM key_state WHERE api_key_id = ?", (api_key_id,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO key_state (api_key_id, state, since) VALUES (?, 'normal', ?)",
                     (api_key_id, now))
        row = conn.execute("SELECT * FROM key_state WHERE api_key_id = ?", (api_key_id,)).fetchone()
    return row


def set_key_state(conn, api_key_id: str, state: str, now: str, incident_id: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO key_state (api_key_id, state, since, incident_id) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(api_key_id) DO UPDATE SET state = excluded.state, since = excluded.since,"
        " incident_id = excluded.incident_id",
        (api_key_id, state, now, incident_id),
    )


def add_event(conn, at: str, kind: str, text: str) -> None:
    conn.execute("INSERT INTO events (at, kind, text) VALUES (?, ?, ?)", (at, kind, text))
