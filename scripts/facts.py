"""Read-only queries for an investigator. Run through scripts/facts.sh.

Uses the sqlite3 CLI with -readonly when it is installed (3.33 or newer, for -json), else
Python sqlite3 with a mode=ro URI. Output is the same either way.
Set FACTS_ENGINE=python or FACTS_ENGINE=cli to force one.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import replaylib  # noqa: E402

USAGE = """usage: scripts/facts.sh <query> [args]

queries:
  key-history [api_key_id]      payments of the key per day and per hour, and its last hour of requests
  beneficiary <beneficiary_id>  who added it, approval, prior payments (count, first, last, min, avg, max)
  bases <beneficiary_id>        approved reasons to pay this beneficiary
  held <incident_id>            the incident and its held payments

"Now" is the request clock: the latest request seen by the breaker (real time if none).
Databases: BANK_DB and BREAKER_DB if set and existing, else bank.db and breaker.db in the
repo root, else a snapshot rebuilt from the committed traces."""

ARG = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_ :.-]+$")


class Db:
    def __init__(self, bank_db: Path, breaker_db: Path):
        self.bank, self.breaker = bank_db.resolve(), breaker_db.resolve()
        self.engine = os.environ.get("FACTS_ENGINE") or ("cli" if cli_ok() else "python")

    def query(self, sql: str, params: dict) -> tuple[list[str], list[list]]:
        return (self._cli if self.engine == "cli" else self._python)(sql, params)

    def _python(self, sql, params):
        conn = sqlite3.connect(self.breaker.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("ATTACH DATABASE ? AS bank", (self.bank.as_uri() + "?mode=ro",))
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _cli(self, sql, params):
        lines = [".parameter init"]
        for name, value in params.items():
            value = str(value)
            if not SAFE_VALUE.match(value):  # never reached for validated arguments
                raise ValueError(f"unsafe parameter value for :{name}")
            lines.append(f".parameter set :{name} \"'{value}'\"")
        bank = str(self.bank).replace("'", "''")
        lines.append(f"ATTACH DATABASE '{bank}' AS bank;")
        lines.append(sql.rstrip().rstrip(";") + ";")
        r = subprocess.run(["sqlite3", "-readonly", "-json", str(self.breaker)],
                           input="\n".join(lines) + "\n", capture_output=True, text=True,
                           timeout=60)
        if r.returncode != 0 or r.stderr.strip():
            raise RuntimeError(f"sqlite3: {r.stderr.strip()}")
        rows = json.loads(r.stdout) if r.stdout.strip() else []
        if rows:
            cols = list(rows[0].keys())
        else:  # no rows: ask for the column names alone
            cols = self._python_cols(sql, params)
        return cols, [[row[c] for c in cols] for row in rows]

    def _python_cols(self, sql, params):
        return self._python(f"SELECT * FROM ({sql.rstrip().rstrip(';')}) LIMIT 0", params)[0]


def cli_ok() -> bool:
    if not shutil.which("sqlite3"):
        return False
    try:
        out = subprocess.run(["sqlite3", "-version"], capture_output=True, text=True,
                             timeout=10).stdout.split()
        major, minor = (int(x) for x in out[0].split(".")[:2])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return False
    return (major, minor) >= (3, 33)


def fmt(v, decimals: bool) -> str:
    """None as '-'; floats with 2 decimals if any value in the column has a fraction."""
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}" if decimals else str(int(v))
    return str(v)


def table(title: str, cols: list[str], rows: list[list]) -> str:
    out = [title]
    if not rows:
        return title + "\n  (none)"
    decimals = [any(isinstance(r[i], float) and not r[i].is_integer() for r in rows)
                for i in range(len(cols))]
    cells = [[fmt(v, d) for v, d in zip(r, decimals)] for r in rows]
    numeric = [all(isinstance(r[i], (int, float)) or r[i] is None for r in rows)
               for i in range(len(cols))]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]

    def line(vals):
        return "  " + "  ".join(v.rjust(w) if n else v.ljust(w)
                                for v, w, n in zip(vals, widths, numeric)).rstrip()
    out.append(line(cols))
    out.append(line(["-" * w for w in widths]))
    out.extend(line(r) for r in cells)
    return "\n".join(out)


def reference_now(db: Db) -> str:
    _, rows = db.query("SELECT MAX(created_at) FROM reservations", {})
    if rows and rows[0][0]:
        return rows[0][0]
    return datetime.now(timezone(timedelta(hours=4))).strftime("%Y-%m-%d %H:%M:%S")


def key_history(db: Db, args: list[str]) -> list[str]:
    if args:
        key = args[0]
    else:
        _, rows = db.query("SELECT api_key_id FROM incidents ORDER BY id DESC LIMIT 1", {})
        key = rows[0][0] if rows else common.API_KEY_ID
    p = {"key": key}
    _, last = db.query("SELECT MAX(created_at) FROM reservations WHERE api_key_id = :key", p)
    p["ref"] = last[0][0] if last and last[0][0] else reference_now(db)
    return [
        "API key " + key + ", request clock now " + p["ref"],
        table("Key", *db.query(
            "SELECT id, client_id, created_at FROM bank.api_keys WHERE id = :key", p)),
        table("Bank payments by day", *db.query(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS payments,"
            " SUM(amount_aed) AS sum_aed, MIN(amount_aed) AS min_aed, MAX(amount_aed) AS max_aed,"
            " COUNT(DISTINCT beneficiary_id) AS beneficiaries,"
            " MIN(substr(created_at, 12)) AS first_time, MAX(substr(created_at, 12)) AS last_time"
            " FROM bank.payments WHERE api_key_id = :key AND created_at <= :ref"
            " GROUP BY day ORDER BY day", p)),
        table("Bank payments by hour of day", *db.query(
            "SELECT substr(created_at, 12, 2) AS hour, COUNT(*) AS payments,"
            " SUM(amount_aed) AS sum_aed, ROUND(AVG(amount_aed), 2) AS avg_aed,"
            " MAX(amount_aed) AS max_aed"
            " FROM bank.payments WHERE api_key_id = :key AND created_at <= :ref"
            " GROUP BY hour ORDER BY hour", p)),
        table("Requests to the breaker in the last hour", *db.query(
            "SELECT created_at, id, beneficiary_id, amount_aed, basis_id, status, rule_id, level,"
            " decided_by, incident_id FROM reservations WHERE api_key_id = :key"
            " AND created_at >= datetime(:ref, '-1 hour') AND created_at <= :ref"
            " ORDER BY created_at, id", p)),
        table("Requests in the last hour by status", *db.query(
            "SELECT status, COUNT(*) AS requests, SUM(amount_aed) AS sum_aed FROM reservations"
            " WHERE api_key_id = :key AND created_at >= datetime(:ref, '-1 hour')"
            " AND created_at <= :ref GROUP BY status ORDER BY status", p)),
    ]


def beneficiary(db: Db, args: list[str]) -> list[str]:
    p = {"ben": args[0], "ref": reference_now(db)}
    return [
        "Beneficiary " + p["ben"] + ", request clock now " + p["ref"],
        table("Beneficiary", *db.query(
            "SELECT id, name, client_id, added_by, approved_at,"
            " CAST(julianday(:ref) - julianday(approved_at) AS INTEGER) AS days_since_approval"
            " FROM bank.beneficiaries WHERE id = :ben", p)),
        table("Bank payments before now", *db.query(
            "SELECT COUNT(*) AS prior_payments, MIN(created_at) AS first_payment,"
            " MAX(created_at) AS last_payment,"
            " CAST(julianday(:ref) - julianday(MAX(created_at)) AS INTEGER) AS last_paid_days_ago,"
            " MIN(amount_aed) AS min_aed, ROUND(AVG(amount_aed), 2) AS avg_aed,"
            " MAX(amount_aed) AS max_aed"
            " FROM bank.payments WHERE beneficiary_id = :ben AND created_at < :ref", p)),
        table("Requests to the breaker by status", *db.query(
            "SELECT status, COUNT(*) AS requests, SUM(amount_aed) AS sum_aed,"
            " MIN(created_at) AS first_request, MAX(created_at) AS last_request"
            " FROM reservations WHERE beneficiary_id = :ben GROUP BY status ORDER BY status", p)),
    ]


def bases(db: Db, args: list[str]) -> list[str]:
    p = {"ben": args[0]}
    return [
        "Bases for beneficiary " + p["ben"],
        table("Bases", *db.query(
            "SELECT id, type, amount_aed, approved, used FROM bank.bases"
            " WHERE beneficiary_id = :ben ORDER BY id", p)),
        table("Totals", *db.query(
            "SELECT COUNT(*) AS bases, COALESCE(SUM(approved = 1 AND used = 0), 0)"
            " AS approved_unused FROM bank.bases WHERE beneficiary_id = :ben", p)),
    ]


def held(db: Db, args: list[str]) -> list[str]:
    p = {"inc": args[0]}
    return [
        "Incident " + p["inc"],
        table("Incident", *db.query(
            "SELECT id, api_key_id, rule_id, first_violation_at, tripped_at, status, verdict,"
            " hold_until, pr_url FROM incidents WHERE id = :inc", p)),
        table("Held payments", *db.query(
            "SELECT r.id, r.created_at, r.beneficiary_id, r.amount_aed, r.basis_id, r.rule_id,"
            " r.level, (SELECT COUNT(*) FROM bank.payments p WHERE p.beneficiary_id = r.beneficiary_id"
            " AND p.created_at < r.created_at) AS prior_payments"
            " FROM reservations r WHERE r.incident_id = :inc AND r.status = 'held'"
            " ORDER BY r.created_at, r.id", p)),
        table("Held payments by beneficiary", *db.query(
            "SELECT beneficiary_id, COUNT(*) AS held, SUM(amount_aed) AS sum_aed FROM reservations"
            " WHERE incident_id = :inc AND status = 'held' GROUP BY beneficiary_id"
            " ORDER BY beneficiary_id", p)),
    ]


QUERIES = {"key-history": (key_history, 0), "beneficiary": (beneficiary, 1),
           "bases": (bases, 1), "held": (held, 1)}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in QUERIES:
        print(USAGE, file=sys.stderr)
        return 2
    fn, needed = QUERIES[argv[0]]
    args = argv[1:]
    if len(args) < needed or len(args) > 1:
        print(USAGE, file=sys.stderr)
        return 2
    for a in args:
        if not ARG.match(a):
            print(f"invalid argument {a!r}: only letters, digits and _ are allowed", file=sys.stderr)
            return 2
    bank_db, breaker_db = replaylib.resolve_databases()
    db = Db(bank_db, breaker_db)
    print("\n\n".join(fn(db, args)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
