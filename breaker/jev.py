"""Layer 2: Jev (TypeSafe decision model). Only ever releases a soft stop, never a hard stop.

assess(facts) returns a JevResult, or None on any failure (the caller keeps the hold).
Modes (JEV_MODE): stub (default, no network), live (may release), shadow (call and log, never release).
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import httpx

JEV_MODE = os.environ.get("JEV_MODE", "stub")
JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")
JEV_T_HISTORY = float(os.environ.get("JEV_T_HISTORY", "0.9"))
JEV_T_BASIS = float(os.environ.get("JEV_T_BASIS", "0.9"))
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "1.0"))

QUESTIONS = {
    "fits_history": {
        "type": "noul",
        "instructions": "The payment is consistent with past payments to this beneficiary "
                        "by amount and frequency.",
    },
    "purpose_matches_basis": {
        "type": "noul",
        "instructions": "The stated purpose describes the same obligation as the approved basis.",
    },
}


@dataclass
class JevResult:
    fits_history: float
    purpose_matches_basis: float
    released: bool  # policy outcome; always False in shadow mode
    mode: str


def _num(x):
    return None if x is None else (int(x) if float(x).is_integer() else round(float(x), 2))


def build_facts(conn, res, now: str) -> dict:
    """Aggregates only, built by SQL on the breaker connection (bank attached as `bank`)."""
    ben = conn.execute(
        "SELECT COUNT(*) AS n, AVG(amount_aed) AS avg_amt, MAX(amount_aed) AS max_amt,"
        " MIN(CAST(strftime('%H', created_at) AS INTEGER)) AS h_lo,"
        " MAX(CAST(strftime('%H', created_at) AS INTEGER)) AS h_hi,"
        " CAST(julianday(date(:now)) - julianday(date(MAX(created_at))) AS INTEGER) AS last_days"
        " FROM bank.payments WHERE beneficiary_id = :b AND created_at < :now",
        {"b": res["beneficiary_id"], "now": now}).fetchone()
    approved = conn.execute(
        "SELECT CAST(julianday(date(:now)) - julianday(date(approved_at)) AS INTEGER)"
        " FROM bank.beneficiaries WHERE id = :b",
        {"b": res["beneficiary_id"], "now": now}).fetchone()
    basis_row = None
    if res["basis_id"]:
        basis_row = conn.execute(
            "SELECT id, type, amount_aed, approved FROM bank.bases WHERE id = ? AND beneficiary_id = ?",
            (res["basis_id"], res["beneficiary_id"])).fetchone()
    win = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount_aed), 0) AS reserved,"
        " COALESCE(SUM(CASE WHEN status = 'executed' THEN amount_aed END), 0) AS executed"
        " FROM reservations WHERE api_key_id = :k"
        " AND status IN ('pending','executed','held','released')"
        " AND created_at >= datetime(:now, '-1 hour') AND created_at <= :now",
        {"k": res["api_key_id"], "now": now}).fetchone()
    balance = conn.execute("SELECT balance_aed FROM bank.accounts WHERE id = ?",
                           (res["account_id"],)).fetchone()
    start_balance = (balance[0] if balance else 0) + win["executed"]

    if basis_row:
        basis = {"type": basis_row["type"], "id": basis_row["id"],
                 "amount_aed": _num(basis_row["amount_aed"]), "approved": bool(basis_row["approved"])}
    else:
        basis = {"type": None, "id": None, "amount_aed": None, "approved": False}
    return {
        "payment": {"amount_aed": _num(res["amount_aed"]), "local_time": now[11:16],
                    "purpose": res["purpose"] or ""},
        "beneficiary": {
            "prior_payments": ben["n"], "avg_amount_aed": _num(ben["avg_amt"]),
            "max_amount_aed": _num(ben["max_amt"]),
            "usual_hours": f"{ben['h_lo']:02d}-{ben['h_hi'] + 1:02d}" if ben["n"] else None,
            "last_paid_days_ago": ben["last_days"],
            "approved_days_ago": approved[0] if approved else None},
        "basis": basis,
        "key": {"payments_last_hour": win["n"],
                "share_of_balance_last_hour": round(win["reserved"] / start_balance, 3)
                if start_balance else None},
    }


def _policy(fits: float, basis: float, mode: str) -> JevResult:
    ok = fits >= JEV_T_HISTORY and basis >= JEV_T_BASIS
    return JevResult(fits, basis, ok and mode != "shadow", mode)


def _stub(facts: dict) -> dict:
    ben = facts.get("beneficiary") or {}
    basis = facts.get("basis") or {}
    pay = facts.get("payment") or {}
    high = ((ben.get("prior_payments") or 0) > 0 and basis.get("approved") is True
            and basis.get("amount_aed") == pay.get("amount_aed"))
    score = 0.95 if high else 0.05
    return {"model": "stub", "answers": {k: {"type": "noul", "noul": score} for k in QUESTIONS}}


def _post(request: dict, key: str) -> dict:
    r = httpx.post(JEV_URL, json=request, timeout=JEV_TIMEOUT_S,
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    return r.json()


def _score(data, qid: str) -> float:
    v = data["answers"][qid]["noul"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise ValueError(f"bad value for {qid}")
    return float(v)


def assess(facts: dict, trace: Optional[dict] = None) -> Optional[JevResult]:
    """Ask Jev about a soft stop. None on any failure: the caller keeps the hold (fail closed).

    If `trace` is given it is filled with mode, request_json, response_json (None on failure)
    and latency_ms, for the jev_calls log. The API key is never part of it.
    """
    mode = JEV_MODE
    request = {"model": JEV_MODEL, "state": facts, "questions": QUESTIONS}
    t0 = time.perf_counter()
    data = None
    try:
        if mode == "stub":
            data = _stub(facts)
        elif mode in ("live", "shadow"):
            key = os.environ.get("TYPESAFE_API_KEY", "")
            if not key:
                raise ValueError("TYPESAFE_API_KEY is not set")
            # httpx timeouts are per phase; the future enforces the 1 s total.
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                data = pool.submit(_post, request, key).result(timeout=JEV_TIMEOUT_S)
            finally:
                pool.shutdown(wait=False)
        else:
            raise ValueError(f"unknown JEV_MODE {mode!r}")
        result = _policy(_score(data, "fits_history"), _score(data, "purpose_matches_basis"), mode)
    except Exception:  # timeout, connection error, bad status, malformed answer
        result = None
    if trace is not None:
        try:
            response_json = json.dumps(data) if data is not None else None
        except (TypeError, ValueError):
            response_json = None
        trace.update(mode=mode, request_json=json.dumps(request), response_json=response_json,
                     latency_ms=round((time.perf_counter() - t0) * 1000))
    return result
