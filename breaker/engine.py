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
    """Set the state of a key. Every change resets the probe counter."""
    conn.execute(
        "INSERT INTO key_state (api_key_id, state, since, incident_id, probe_clean)"
        " VALUES (?, ?, ?, ?, 0)"
        " ON CONFLICT(api_key_id) DO UPDATE SET state = excluded.state, since = excluded.since,"
        " incident_id = excluded.incident_id, probe_clean = 0",
        (api_key_id, state, now, incident_id),
    )


def add_event(conn, at: str, kind: str, text: str) -> None:
    conn.execute("INSERT INTO events (at, kind, text) VALUES (?, ?, ?)", (at, kind, text))


# --- hold expiry and probe (milestone 6) --------------------------------------

def _reject_expired(conn, rows, now: str, why: str) -> list[str]:
    ids = []
    for r in rows:
        set_status(conn, r["id"], "rejected", now, "expiry")
        add_event(conn, now, "expiry",
                  f"hold {r['id']} expired ({why}): AED {_num(r['amount_aed'])} to "
                  f"{r['beneficiary_id']} rejected, never sent")
        ids.append(r["id"])
    return ids


def _num(x):
    return int(x) if float(x).is_integer() else x


def expire_holds(conn, api_key_id: str, now: str, ttl_min: int) -> dict:
    """Lazy hold expiry on the request clock. Call inside BEGIN IMMEDIATE.

    It runs only when a payout, a verdict or an admin call for this key is processed, with
    that request's "now". Nothing runs on a timer or on wall-clock time.

    - Holds of the key's open incident share one deadline: the latest of them plus
      ttl_min, or the incident's hold_until if that is later. So every hold is older than
      ttl_min when it expires, and a key that keeps sending requests while paused stays
      paused. When the deadline has passed, all of them are rejected, the incident gets
      status `expired`, and a paused key moves to `probe`.
    - Any other hold of the key (a soft stop with no incident, a probe hold) expires on
      its own, ttl_min after it was created. The key state does not change.

    A deadline passes only when `now` is strictly later, so a clock that goes back
    expires nothing. Returns {"rejected": [...], "incidents": [...], "probe": bool}.
    """
    ttl = f"+{int(ttl_min)} minutes"
    out = {"rejected": [], "incidents": [], "probe": False}
    for inc in conn.execute("SELECT id, hold_until FROM incidents WHERE api_key_id = ?"
                            " AND status = 'open' ORDER BY id", (api_key_id,)).fetchall():
        latest = conn.execute("SELECT datetime(MAX(created_at), ?) FROM reservations"
                              " WHERE incident_id = ? AND status = 'held'",
                              (ttl, inc["id"])).fetchone()[0]
        if latest is None:
            continue
        deadline = max(latest, inc["hold_until"] or "")
        if not now > deadline:
            continue
        rows = conn.execute("SELECT * FROM reservations WHERE incident_id = ? AND status = 'held'"
                            " ORDER BY created_at, id", (inc["id"],)).fetchall()
        out["rejected"] += _reject_expired(conn, rows, now, f"{inc['id']}, limit {deadline}")
        conn.execute("UPDATE incidents SET status = 'expired' WHERE id = ?", (inc["id"],))
        add_event(conn, now, "expiry", f"incident {inc['id']} expired at {deadline}: "
                                       f"{len(rows)} held payments rejected, never sent")
        out["incidents"].append(inc["id"])
    rows = conn.execute(
        "SELECT * FROM reservations r WHERE r.api_key_id = ? AND r.status = 'held'"
        " AND datetime(r.created_at, ?) < ?"
        " AND NOT EXISTS (SELECT 1 FROM incidents i WHERE i.id = r.incident_id"
        "                 AND i.status = 'open')"
        " ORDER BY r.created_at, r.id", (api_key_id, ttl, now)).fetchall()
    out["rejected"] += _reject_expired(conn, rows, now, f"limit {ttl_min} min")
    ks = conn.execute("SELECT * FROM key_state WHERE api_key_id = ?", (api_key_id,)).fetchone()
    if ks is not None and ks["state"] == "paused" and ks["incident_id"] in out["incidents"]:
        set_key_state(conn, api_key_id, "probe", now, ks["incident_id"])
        add_event(conn, now, "probe", f"key {api_key_id} moved to probe after {ks['incident_id']}"
                                      f" expired")
        out["probe"] = True
    return out


def probe_gate(conn, beneficiary_id: str, amount_aed: float, now: str,
               max_amount_aed: float) -> Optional[str]:
    """None if a payment may pass a key in probe, else why it is held."""
    prior = conn.execute("SELECT COUNT(*) FROM bank.payments WHERE beneficiary_id = ?"
                         " AND created_at < ?", (beneficiary_id, now)).fetchone()[0]
    if prior == 0:
        return "probe: beneficiary has no prior payments"
    if amount_aed > max_amount_aed:
        return f"probe: amount {_num(amount_aed)} above {_num(max_amount_aed)}"
    return None


def count_probe_clean(conn, api_key_id: str, since: str, now: str, target: int) -> bool:
    """Count one clean payment of a key in probe. Call inside BEGIN IMMEDIATE.

    `since` is the probe start seen by the request, so a payment from an earlier probe
    period never counts in a later one. Returns True if the key went back to normal.
    """
    cur = conn.execute("UPDATE key_state SET probe_clean = probe_clean + 1 WHERE api_key_id = ?"
                       " AND state = 'probe' AND since = ?", (api_key_id, since))
    if cur.rowcount == 0:
        return False
    n = conn.execute("SELECT probe_clean FROM key_state WHERE api_key_id = ?",
                     (api_key_id,)).fetchone()[0]
    if n < target:
        return False
    set_key_state(conn, api_key_id, "normal", now, None)
    add_event(conn, now, "probe", f"key {api_key_id} back to normal after {n} clean payments"
                                  f" in probe")
    return True
