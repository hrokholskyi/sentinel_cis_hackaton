"""Dashboard data for GET /state, and the in-memory presenter state.

Everything the page shows is computed here, in SQL or Python, so the page only renders.
All reads use the breaker connection with bank.db attached read-only as schema `bank`.
"""

import json
import os
import statistics
import threading
from typing import Optional

from breaker import incidents

STREAM_ROWS = 60
HOLD_ROWS = 15


def _num(x):
    return None if x is None else incidents.num(x)


# --- counters -------------------------------------------------------------------

def stopped(conn) -> dict:
    """Payments on hold now: count and AED sum. An expired hold is rejected, so not stopped."""
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_aed), 0) s FROM reservations"
                     " WHERE status = 'held'").fetchone()
    return {"count": r["n"], "aed": _num(r["s"])}


def leaked(conn) -> dict:
    """Executed payments of a tripped key in the hour up to its incident's tripped_at.

    Payments sent later by a verdict or an admin are explicit decisions, not leaks.
    An incident closed by a full `release` verdict was legitimate traffic (the payroll
    burst), so the payments before it are not leaks either.
    EXISTS counts a payment once even if two incidents cover it.
    """
    r = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(r.amount_aed), 0) s FROM reservations r"
        " WHERE r.status = 'executed' AND COALESCE(r.decided_by, '') NOT IN ('verdict', 'admin')"
        " AND EXISTS (SELECT 1 FROM incidents i WHERE i.api_key_id = r.api_key_id"
        "             AND NOT (i.status = 'closed' AND COALESCE(i.verdict, '') = 'release')"
        "             AND r.created_at >= datetime(i.tripped_at, '-1 hour')"
        "             AND r.created_at <= i.tripped_at)").fetchone()
    return {"count": r["n"], "aed": _num(r["s"])}


def expired(conn) -> dict:
    """Holds rejected because their time limit passed (decided_by = expiry): never sent."""
    r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_aed), 0) s FROM reservations"
                     " WHERE status = 'rejected' AND decided_by = 'expiry'").fetchone()
    return {"count": r["n"], "aed": _num(r["s"])}


def legit_held(conn) -> dict:
    """Held payments that would pass the release re-check of a verdict (incidents.recheck)."""
    ids = [row["id"] for row in conn.execute("SELECT * FROM reservations WHERE status = 'held'")
           if incidents.recheck(conn, row)[0] is not None]
    return {"count": len(ids), "reservation_ids": ids}


def scores(response_json: Optional[str]) -> Optional[tuple[float, float]]:
    """(fits_history, purpose_matches_basis) from a logged Jev answer, None if there is none."""
    try:
        answers = json.loads(response_json)["answers"]
        out = []
        for qid in ("fits_history", "purpose_matches_basis"):
            v = answers[qid]["noul"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                return None
            out.append(float(v))
        return out[0], out[1]
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def released_by_jev(conn) -> dict:
    """Jev calls that released a payment; median latency over calls that got an answer."""
    rows = conn.execute("SELECT released, latency_ms, response_json FROM jev_calls").fetchall()
    lat = [r["latency_ms"] for r in rows
           if r["latency_ms"] is not None and scores(r["response_json"]) is not None]
    return {"count": sum(1 for r in rows if r["released"]),
            "median_latency_ms": round(statistics.median(lat)) if lat else None,
            "calls": len(rows), "answered": len(lat)}


# --- rows -----------------------------------------------------------------------

_ROW_SQL = (
    "SELECT r.*, b.name AS beneficiary_name,"
    " NOT EXISTS (SELECT 1 FROM bank.payments p WHERE p.beneficiary_id = r.beneficiary_id"
    "             AND p.created_at < r.created_at) AS first_payment"
    " FROM reservations r LEFT JOIN bank.beneficiaries b ON b.id = r.beneficiary_id")


def _row(r) -> dict:
    return {"id": r["id"], "created_at": r["created_at"], "beneficiary_id": r["beneficiary_id"],
            "beneficiary_name": r["beneficiary_name"] or r["beneficiary_id"],
            "amount_aed": _num(r["amount_aed"]), "purpose": r["purpose"], "status": r["status"],
            "rule_id": r["rule_id"], "level": r["level"], "detail": r["detail"],
            "decided_by": r["decided_by"], "incident_id": r["incident_id"],
            "first_payment": bool(r["first_payment"])}


def stream(conn, limit: int = STREAM_ROWS) -> list[dict]:
    """Latest reservations in arrival order, newest first (the demo clock jumps back and forth)."""
    return [_row(r) for r in conn.execute(_ROW_SQL + " ORDER BY r.rowid DESC LIMIT ?", (limit,))]


def hold_queue(conn, limit: int = HOLD_ROWS) -> list[dict]:
    return [_row(r) for r in conn.execute(_ROW_SQL + " WHERE r.status = 'held'"
                                                     " ORDER BY r.rowid DESC LIMIT ?", (limit,))]


# --- layers ---------------------------------------------------------------------

def layer1(conn, rules_loaded: int) -> dict:
    """The last rule result: the latest reservation and what layer 1 decided for it."""
    rows = stream(conn, 1)
    out = {"rules_loaded": rules_loaded, "last": rows[0] if rows else None, "result": None}
    if rows:
        r = rows[0]
        if r["rule_id"] == "key_paused":
            out["result"] = "paused"
        elif r["rule_id"] == "probe":
            out["result"] = "probe"
        elif r["level"] in ("soft", "hard"):
            out["result"] = r["level"]
        else:
            out["result"] = "pass"
    return out


def layer2(conn) -> dict:
    """The last Jev call: both scores, thresholds, latency, mode and outcome."""
    from breaker import jev  # thresholds live in the Jev module config

    out = {"t_history": jev.JEV_T_HISTORY, "t_basis": jev.JEV_T_BASIS, "mode": jev.JEV_MODE,
           "last": None}
    j = conn.execute("SELECT * FROM jev_calls ORDER BY id DESC LIMIT 1").fetchone()
    if j is None:
        return out
    res = conn.execute(_ROW_SQL + " WHERE r.id = ?", (j["reservation_id"],)).fetchone()
    sc = scores(j["response_json"])
    if j["released"]:
        outcome = "released"
    elif sc is None:
        outcome = "no_answer"
    else:
        outcome = "kept"
    out["last"] = {"id": j["id"], "reservation": _row(res) if res else None, "mode": j["mode"],
                   "latency_ms": j["latency_ms"], "released": bool(j["released"]),
                   "fits_history": sc[0] if sc else None,
                   "purpose_matches_basis": sc[1] if sc else None, "outcome": outcome}
    return out


def webhook_status(conn, incident_id: Optional[str]) -> dict:
    """not_configured, pending (no outcome logged yet), sent or failed, with the event text."""
    if not os.environ.get("DEVIN_WEBHOOK_URL", "").strip():
        return {"status": "not_configured", "text": None}
    if not incident_id:
        return {"status": None, "text": None}
    ev = conn.execute("SELECT kind, text FROM events WHERE text LIKE ? AND kind IN"
                      " ('webhook', 'error') ORDER BY id DESC LIMIT 1",
                      (f"webhook for {incident_id} %",)).fetchone()
    if ev is None:
        return {"status": "pending", "text": None}
    return {"status": "sent" if ev["kind"] == "webhook" else "failed", "text": ev["text"]}


def incident_card(conn, inc: Optional[dict]) -> Optional[dict]:
    if inc is None:
        return None
    held = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_aed), 0) s FROM reservations"
                        " WHERE incident_id = ? AND status = 'held'", (inc["id"],)).fetchone()
    gone = conn.execute("SELECT COUNT(*) FROM reservations WHERE incident_id = ?"
                        " AND status = 'rejected' AND decided_by = 'expiry'",
                        (inc["id"],)).fetchone()[0]
    return {"id": inc["id"], "api_key_id": inc["api_key_id"], "rule_id": inc["rule_id"],
            "expired_count": gone, "tripped_at": inc["tripped_at"], "tripped_wall_at": inc["tripped_wall_at"],
            "first_violation_at": inc["first_violation_at"],
            "violation_to_pause_ms": inc["violation_to_pause_ms"], "status": inc["status"],
            "verdict": inc["verdict"], "requested_verdict": inc["requested_verdict"],
            "verdict_at": inc["verdict_at"], "pr_url": inc["pr_url"],
            "pause_to_pr_min": incidents.pause_to_pr_min(inc),
            "held_count": held["n"], "held_aed": _num(held["s"])}


# --- presenter (in memory, one breaker process) ---------------------------------

SPOTLIGHTS = ("all", "layers", "layer1", "layer2", "layer3", "key", "counters", "stream", "queue",
              "incident", "events")
PRESENTER_FIELDS = ("caption", "spotlight", "card", "step", "step_done", "panel")
_presenter: dict = dict.fromkeys(PRESENTER_FIELDS)
_presenter_lock = threading.Lock()


def presenter() -> dict:
    with _presenter_lock:
        out = dict(_presenter)
    out["step_done"] = out["step_done"] or []
    out["active"] = any(out[f] not in (None, []) for f in PRESENTER_FIELDS)
    return out


def presenter_update(fields: dict) -> dict:
    """Set the given fields; a None value clears that field, missing fields keep their value."""
    with _presenter_lock:
        for name, value in fields.items():
            if name in PRESENTER_FIELDS:
                _presenter[name] = value
    return presenter()


def presenter_clear() -> dict:
    with _presenter_lock:
        _presenter.update(dict.fromkeys(PRESENTER_FIELDS))
    return presenter()


# --- everything ---------------------------------------------------------------

def build(conn, *, incs: list[dict], keys: list[dict], rules_loaded: int,
          probe_target: Optional[int] = None) -> dict:
    """The dashboard fields added to GET /state."""
    key = conn.execute(
        "SELECT k.id, k.client_id, c.name FROM bank.api_keys k"
        " LEFT JOIN bank.clients c ON c.id = k.client_id ORDER BY k.id LIMIT 1").fetchone()
    key_id = key["id"] if key else None
    ks = next((k for k in keys if k["api_key_id"] == key_id), None)
    last = conn.execute("SELECT created_at FROM reservations ORDER BY rowid DESC LIMIT 1").fetchone()
    inc = next((i for i in reversed(incs) if i["status"] == "open"), incs[-1] if incs else None)
    from breaker import jev
    return {
        "bank_name": os.environ.get("BANK_NAME", "").strip() or "ADCB",
        "client": {"id": key["client_id"], "name": key["name"]} if key else None,
        "key": {"api_key_id": key_id, "state": ks["state"] if ks else "normal",
                "since": ks["since"] if ks else None,
                "incident_id": ks["incident_id"] if ks else None,
                "probe_clean": ks.get("probe_clean", 0) if ks else 0,
                "probe_target": probe_target},
        "clock": last["created_at"] if last else None,
        "jev_mode": jev.JEV_MODE,
        "counters": {
            "stopped": stopped(conn),
            "leaked": leaked(conn),
            "expired": expired(conn),
            "legit_held": legit_held(conn),
            "released_by_jev": released_by_jev(conn),
            "violation_to_pause_ms": inc["violation_to_pause_ms"] if inc else None,
        },
        "stream": stream(conn),
        "hold_queue": hold_queue(conn),
        "incident": incident_card(conn, inc),
        "layers": {
            "layer1": layer1(conn, rules_loaded),
            "layer2": layer2(conn),
            "layer3": {"incident_id": inc["id"] if inc else None,
                       "webhook": webhook_status(conn, inc["id"] if inc else None)},
        },
        "presenter": presenter(),
    }
