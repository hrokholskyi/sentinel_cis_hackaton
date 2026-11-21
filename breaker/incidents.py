"""Incident record, trace file, webhook and verdict checks.

On the first hard violation for a key an incident is created and the key's requests from
the last hour are written to <INCIDENTS_DIR>/<incident_id>.jsonl. Every later request from
that key is appended while the incident is open. Held payments carry the incident id.

create() and append() run inside the caller's BEGIN IMMEDIATE transaction, so the trace file
is written in the same order as the reservations are committed. The webhook runs after the
commit, in a background thread.
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

TS = "%Y-%m-%d %H:%M:%S"


def wall_now() -> str:
    """Real time as a UTC ISO 8601 string, independent of the simulated request clock."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(x):
    return int(x) if float(x).is_integer() else x


def trace_line(res) -> dict:
    """Same line format as the traffic scripts: {sim_time, request, status, reservation_id}."""
    request = {
        "account_id": res["account_id"],
        "beneficiary_id": res["beneficiary_id"],
        "amount_aed": num(res["amount_aed"]),
        "purpose": res["purpose"],
    }
    if res["basis_id"]:
        request["basis_id"] = res["basis_id"]
    sim_time = res["created_at"].replace(" ", "T") + "+04:00"
    return {"sim_time": sim_time, "request": request, "status": res["status"],
            "reservation_id": res["id"]}


def open_incident(conn, api_key_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM incidents WHERE api_key_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (api_key_id,),
    ).fetchone()
    return dict(row) if row else None


def create(conn, *, api_key_id: str, rule_id: str, now: str, processing_ms: float,
           incidents_dir: str) -> dict:
    """Create the incident for a key's first hard violation and write its trace file."""
    n = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] + 1
    incident_id = f"inc_{n:04d}"
    first = conn.execute(
        "SELECT MIN(created_at) FROM reservations WHERE api_key_id = ? AND level IS NOT NULL"
        " AND created_at >= datetime(?, '-1 hour') AND created_at <= ?",
        (api_key_id, now, now),
    ).fetchone()[0] or now
    gap_ms = (datetime.strptime(now, TS) - datetime.strptime(first, TS)).total_seconds() * 1000
    trace_file = os.path.join(incidents_dir, f"{incident_id}.jsonl")
    conn.execute(
        "INSERT INTO incidents (id, api_key_id, rule_id, tripped_at, first_violation_at, status,"
        " violation_to_pause_ms, trace_file, tripped_wall_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
        (incident_id, api_key_id, rule_id, now, first, round(gap_ms + processing_ms), trace_file,
         wall_now()),
    )
    # Holds of this key from the last hour (soft stops and the tripping payment) join it.
    conn.execute(
        "UPDATE reservations SET incident_id = ? WHERE api_key_id = ? AND status = 'held'"
        " AND incident_id IS NULL AND created_at >= datetime(?, '-1 hour') AND created_at <= ?",
        (incident_id, api_key_id, now, now),
    )
    rows = conn.execute(
        "SELECT * FROM reservations WHERE api_key_id = ?"
        " AND created_at >= datetime(?, '-1 hour') AND created_at <= ? ORDER BY created_at, id",
        (api_key_id, now, now),
    ).fetchall()
    try:  # a failed trace write must not undo the hold or the pause
        os.makedirs(incidents_dir, exist_ok=True)
        with open(trace_file, "w") as f:
            for r in rows:
                f.write(json.dumps(trace_line(r)) + "\n")
    except OSError as e:
        conn.execute("INSERT INTO events (at, kind, text) VALUES (?, 'error', ?)",
                     (now, f"trace file {trace_file} not written: {e}"))
    return open_incident(conn, api_key_id)


def append(conn, incident: dict, res, now: str) -> None:
    """Append one later request of the key to the open incident's trace file.

    A held request also attaches to the incident, so a verdict can find it.
    """
    if res["status"] == "held":
        conn.execute("UPDATE reservations SET incident_id = ? WHERE id = ?",
                     (incident["id"], res["id"]))
    try:
        with open(incident["trace_file"], "a") as f:
            f.write(json.dumps(trace_line(res)) + "\n")
    except OSError as e:
        conn.execute("INSERT INTO events (at, kind, text) VALUES (?, 'error', ?)",
                     (now, f"trace file {incident['trace_file']} not appended: {e}"))


# --- webhook (SPEC section 9) -------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBHOOK_TIMEOUT_S = 3.0
COUNTED = ("pending", "executed", "held", "released")
_WINDOW = re.compile(r"datetime\(\s*:now\s*,\s*'-\s*(\d+)\s*(second|minute|hour|day)s?\s*'\s*\)",
                     re.IGNORECASE)
_UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def window_seconds(rule_sql: Optional[str]) -> int:
    """Length of a rule's time window, from its first datetime(:now, '-N unit'). Default 1 hour."""
    m = _WINDOW.search(rule_sql or "")
    return int(m.group(1)) * _UNIT_S[m.group(2).lower()] if m else 3600


def iso_local(ts: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' Gulf time -> ISO 8601 with +04:00."""
    return ts.replace(" ", "T") + "+04:00"


def repo_path(path: str) -> str:
    """A path relative to the repo root when it lies inside the repo, else unchanged."""
    full = os.path.abspath(path)
    if os.path.commonpath([full, ROOT]) == ROOT:
        return os.path.relpath(full, ROOT)
    return path


def webhook_body(conn, incident_id: str, window_s: int) -> dict:
    """The breaker.tripped event. All counts use the request clock, up to tripped_at."""
    inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    key, now = inc["api_key_id"], inc["tripped_at"]
    since = f"-{window_s} seconds"
    marks = ",".join("?" * len(COUNTED))
    win = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(amount_aed), 0) s FROM reservations"
        f" WHERE api_key_id = ? AND status IN ({marks})"
        f" AND created_at >= datetime(?, ?) AND created_at <= ?",
        (key, *COUNTED, now, since, now),
    ).fetchone()
    new_bens = conn.execute(
        f"SELECT COUNT(DISTINCT r.beneficiary_id) FROM reservations r"
        f" WHERE r.api_key_id = ? AND r.status IN ({marks})"
        f" AND r.created_at >= datetime(?, ?) AND r.created_at <= ?"
        f" AND NOT EXISTS (SELECT 1 FROM bank.payments p WHERE p.beneficiary_id = r.beneficiary_id"
        f"                 AND p.created_at < datetime(?, ?))",
        (key, *COUNTED, now, since, now, now, since),
    ).fetchone()[0]
    last_hour = "AND created_at >= datetime(?, '-1 hour') AND created_at <= ?"
    held_n, leaked_n = (conn.execute(
        f"SELECT COUNT(*) FROM reservations WHERE api_key_id = ? AND status = ? {last_hour}",
        (key, status, now, now)).fetchone()[0] for status in ("held", "executed"))
    jev = conn.execute(
        "SELECT COUNT(*) asked, COALESCE(SUM(j.released), 0) released FROM jev_calls j"
        " JOIN reservations r ON r.id = j.reservation_id"
        " WHERE r.api_key_id = ? AND r.created_at >= datetime(?, '-1 hour') AND r.created_at <= ?",
        (key, now, now),
    ).fetchone()
    rows = conn.execute(
        f"SELECT * FROM reservations WHERE api_key_id = ? {last_hour} ORDER BY created_at, id LIMIT 20",
        (key, now, now),
    ).fetchall()
    level = conn.execute("SELECT level FROM reservations WHERE incident_id = ? AND rule_id = ?"
                         " ORDER BY created_at, id LIMIT 1",
                         (incident_id, inc["rule_id"])).fetchone()
    return {
        "event": "breaker.tripped",
        "incident_id": incident_id,
        "tripped_at": iso_local(now),
        "rule": inc["rule_id"],
        "level": level["level"] if level else "hard",
        "api_key_id": key,
        "endpoint": "POST /payouts",
        "window": {"seconds": window_s, "count": win["n"], "sum_aed": num(win["s"]),
                   "new_beneficiaries": new_bens},
        "held_count": held_n,
        "leaked_count": leaked_n,
        "jev": {"model": os.environ.get("JEV_MODEL", "jev-1.13.0"), "asked": jev["asked"],
                "released": jev["released"]},
        "trace_file": repo_path(inc["trace_file"]),
        "sample_requests": [trace_line(r) for r in rows],
    }


def send_webhook(connect, incident_id: str, window_s: int) -> None:
    """POST the event to DEVIN_WEBHOOK_URL and log the outcome as an events row.

    Never raises: a failed webhook must not change the hold, the pause or the HTTP response.
    """
    import httpx

    url = os.environ.get("DEVIN_WEBHOOK_URL", "").strip()
    secret = os.environ.get("DEVIN_WEBHOOK_SECRET", "")
    conn, at = None, None
    try:
        conn = connect()
        at = conn.execute("SELECT tripped_at FROM incidents WHERE id = ?",
                          (incident_id,)).fetchone()[0]
        body = webhook_body(conn, incident_id, window_s)
        headers = {"X-Webhook-Secret": secret} if secret else {}
        r = httpx.post(url, json=body, headers=headers, timeout=WEBHOOK_TIMEOUT_S)
        if 200 <= r.status_code < 300:
            kind, text = "webhook", f"webhook for {incident_id} sent (HTTP {r.status_code})"
        else:
            kind, text = "error", f"webhook for {incident_id} failed: HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001 - any failure is only logged
        kind, text = "error", f"webhook for {incident_id} failed: {type(e).__name__}: {e}"
    try:
        if conn is None:
            conn = connect()
        conn.execute("INSERT INTO events (at, kind, text) VALUES (?, ?, ?)",
                     (at or datetime.now().strftime(TS), kind, text))
    except Exception:  # noqa: BLE001 - nothing left to report to
        pass
    finally:
        if conn is not None:
            conn.close()


def notify(connect, incident_id: str, rule_sql: Optional[str]) -> None:
    """Send the webhook from a background thread, only if DEVIN_WEBHOOK_URL is set.

    Call after the tripping transaction has committed.
    """
    if not os.environ.get("DEVIN_WEBHOOK_URL", "").strip():
        return
    threading.Thread(target=send_webhook, args=(connect, incident_id, window_seconds(rule_sql)),
                     name=f"webhook-{incident_id}", daemon=True).start()


# --- verdicts (SPEC section 9) ------------------------------------------------

def held_payments(conn, incident_id: str) -> list:
    return conn.execute("SELECT * FROM reservations WHERE incident_id = ? AND status = 'held'"
                        " ORDER BY created_at, id", (incident_id,)).fetchall()


def recheck(conn, res, basis_required: bool = False) -> tuple[Optional[str], str]:
    """Re-check one held payment for a release. Returns (basis_id or None, why).

    Used by a release verdict and, with basis_required=True, by a Jev release.
    It passes only if the beneficiary has at least one bank payment before the reservation
    was created, and an approved, unused basis of the key's client exists for this
    beneficiary with exactly the same amount. If the reservation names a basis, it must be
    that one; with basis_required it must name one. A basis carried by another payment in
    flight in the breaker, or already sent by it, does not count.
    """
    if basis_required and not res["basis_id"]:
        return None, "the payment names no basis"
    prior = conn.execute("SELECT COUNT(*) FROM bank.payments WHERE beneficiary_id = ?"
                         " AND created_at < ?",
                         (res["beneficiary_id"], res["created_at"])).fetchone()[0]
    if prior == 0:
        return None, "beneficiary has no prior payments"
    basis = conn.execute(
        "SELECT b.id FROM bank.bases b JOIN bank.api_keys k ON k.client_id = b.client_id"
        " WHERE k.id = ? AND b.beneficiary_id = ? AND b.approved = 1 AND b.used = 0"
        " AND b.amount_aed = ? AND (? IS NULL OR b.id = ?)"
        " AND b.id NOT IN (SELECT basis_id FROM reservations WHERE basis_id IS NOT NULL"
        "                  AND status IN ('pending', 'released', 'executed') AND id != ?)"
        " ORDER BY b.id LIMIT 1",
        (res["api_key_id"], res["beneficiary_id"], res["amount_aed"], res["basis_id"],
         res["basis_id"], res["id"]),
    ).fetchone()
    if basis is None:
        if res["basis_id"]:
            return None, f"basis {res['basis_id']} is not approved, unused and of the same amount"
        return None, "no approved unused basis with the same amount"
    return basis["id"], f"{prior} prior payments, basis {basis['id']}"


def next_morning(now: str) -> str:
    """The next 08:00 on the request clock."""
    ts = datetime.strptime(now, TS)
    morning = ts.replace(hour=8, minute=0, second=0)
    if ts >= morning:
        morning += timedelta(days=1)
    return morning.strftime(TS)


def request_clock(conn, inc) -> str:
    """'Now' for a verdict sent without X-Sim-Time: the latest request time of the key."""
    last = conn.execute("SELECT MAX(created_at) FROM reservations WHERE api_key_id = ?",
                        (inc["api_key_id"],)).fetchone()[0]
    return max(last or inc["tripped_at"], inc["tripped_at"])


def record_verdict(conn, incident_id: str, *, requested: str, verdict: str, reason: str,
                   evidence: dict, pr_url: Optional[str], now: str, status: str,
                   hold_until: Optional[str] = None) -> bool:
    """Record a verdict on an incident that is still open. False if it no longer is (for
    example an admin resume closed it while a release ran): then nothing changes."""
    cur = conn.execute(
        "UPDATE incidents SET verdict = ?, requested_verdict = ?, reason = ?, evidence_json = ?,"
        " pr_url = COALESCE(?, pr_url), verdict_at = ?, verdict_wall_at = ?, status = ?,"
        " hold_until = COALESCE(?, hold_until) WHERE id = ? AND status = 'open'",
        (verdict, requested, reason, json.dumps(evidence, sort_keys=True), pr_url, now,
         wall_now(), status, hold_until, incident_id),
    )
    return cur.rowcount > 0


def pause_to_pr_min(inc: dict) -> Optional[float]:
    """Real minutes from the pause to the verdict that carries a PR link, one decimal."""
    if not (inc.get("pr_url") and inc.get("tripped_wall_at") and inc.get("verdict_wall_at")):
        return None
    gap = (datetime.fromisoformat(inc["verdict_wall_at"])
           - datetime.fromisoformat(inc["tripped_wall_at"])).total_seconds()
    return round(gap / 60, 1)
