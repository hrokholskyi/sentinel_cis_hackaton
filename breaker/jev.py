"""Layer 2: Jev (TypeSafe decision model). Only ever releases a soft stop, never a hard stop.

assess(facts) returns a JevResult, or None on any failure (the caller keeps the hold).
Modes (JEV_MODE): stub (default, no network), live (may release), shadow (call and log, never release).
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

JEV_MODE = os.environ.get("JEV_MODE", "stub")
JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.14.0")  # pinned, not the jev-latest alias
JEV_T_HISTORY = float(os.environ.get("JEV_T_HISTORY", "0.9"))
JEV_T_BASIS = float(os.environ.get("JEV_T_BASIS", "0.9"))
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "1.0"))
JEV_KEEPALIVE_S = 300.0  # keep the idle TLS connection (httpx default is 5 s)

_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jev")

# Same two questions as SPEC section 7, with the yes/no boundary spelled out in `criteria`.
# Time of day is left out on purpose: layer 1 already held the payment for it.
QUESTIONS = {
    "fits_history": {
        "type": "noul",
        "instructions": "Judge whether `payment` is consistent with this client's established "
                        "payment history with `beneficiary`, by amount and by frequency. Time of "
                        "day is checked elsewhere and is not part of this question.",
        "criteria": {
            "true": "There is an established history of payments to this beneficiary spread over "
                    "weeks or months, and the payment amount lies within the range of those "
                    "earlier payments.",
            "false": "There is little or no earlier history, the earlier payments were all made "
                     "within the last day, or the amount is outside the range of earlier payments.",
        },
    },
    "purpose_matches_basis": {
        "type": "noul",
        "instructions": "Judge whether `payment.purpose` describes the same obligation as the "
                        "approved basis in `basis` (its description, type and amount).",
        "criteria": {
            "true": "An approved basis exists, the purpose refers to the same document and the "
                    "same goods or services as `basis.description`, and the payment amount equals "
                    "`basis.amount_aed`.",
            "false": "There is no approved basis, the purpose describes something else, or the "
                     "amount differs from the basis amount.",
        },
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
    """Aggregates only, built by SQL on the breaker connection (bank attached as `bank`).

    No raw history and no verdict: counts, amounts, day counts and record attributes.
    """
    prior = "FROM bank.payments p WHERE p.beneficiary_id = :b AND p.created_at < :now"
    args = {"b": res["beneficiary_id"], "now": now}
    ben = conn.execute(
        "SELECT COUNT(*) AS n, AVG(amount_aed) AS avg_amt, MIN(amount_aed) AS min_amt,"
        " MAX(amount_aed) AS max_amt,"
        " MIN(CAST(strftime('%H', created_at) AS INTEGER)) AS h_lo,"
        " MAX(CAST(strftime('%H', created_at) AS INTEGER)) AS h_hi,"
        " CAST(julianday(date(:now)) - julianday(date(MAX(created_at))) AS INTEGER) AS last_days,"
        " CAST(julianday(date(:now)) - julianday(date(MIN(created_at))) AS INTEGER) AS first_days,"
        " SUM(created_at >= datetime(:now, '-1 day')) AS n_24h,"
        " AVG(EXISTS (SELECT 1 FROM bank.bases s WHERE s.id = p.basis_id AND s.approved = 1))"
        "   AS share_basis " + prior, args).fetchone()
    median = conn.execute(
        "SELECT AVG(amount_aed) FROM (SELECT amount_aed " + prior +
        " ORDER BY amount_aed LIMIT 2 - :n % 2 OFFSET (:n - 1) / 2)",
        {**args, "n": ben["n"]}).fetchone()[0] if ben["n"] else None
    who = conn.execute(
        "SELECT name, CAST(julianday(date(:now)) - julianday(date(approved_at)) AS INTEGER)"
        " FROM bank.beneficiaries WHERE id = :b", args).fetchone()
    basis_row = None
    if res["basis_id"]:
        basis_row = conn.execute(
            "SELECT id, type, amount_aed, approved, used, description FROM bank.bases"
            " WHERE id = ? AND beneficiary_id = ?",
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
                 "description": basis_row["description"],
                 "amount_aed": _num(basis_row["amount_aed"]), "approved": bool(basis_row["approved"]),
                 "already_used": bool(basis_row["used"])}
    else:
        basis = {"type": None, "id": None, "description": None, "amount_aed": None,
                 "approved": False, "already_used": None}
    n = ben["n"]
    return {
        "payment": {"amount_aed": _num(res["amount_aed"]), "local_time": now[11:16],
                    "purpose": res["purpose"] or ""},
        "beneficiary": {
            "name": who[0] if who else None,
            "approved_days_ago": who[1] if who else None,
            "prior_payments": n,
            "prior_payments_last_24h": ben["n_24h"] or 0,
            "min_amount_aed": _num(ben["min_amt"]), "median_amount_aed": _num(median),
            "avg_amount_aed": _num(ben["avg_amt"]), "max_amount_aed": _num(ben["max_amt"]),
            "amount_within_prior_range": (ben["min_amt"] <= res["amount_aed"] <= ben["max_amt"])
            if n else None,
            "usual_hours": f"{ben['h_lo']:02d}-{ben['h_hi'] + 1:02d}" if n else None,
            "first_paid_days_ago": ben["first_days"],
            "last_paid_days_ago": ben["last_days"],
            "share_with_approved_basis": None if ben["share_basis"] is None
            else round(ben["share_basis"], 2)},
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


def _http() -> httpx.Client:
    """One client for the life of the process, so calls reuse the TLS connection."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=JEV_TIMEOUT_S,
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=JEV_KEEPALIVE_S))
        return _client


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post(request: dict, key: str) -> dict:
    r = _http().post(JEV_URL, json=request, headers=_headers(key))
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    return r.json()


def warm_up() -> int:
    """Open the connection with a cheap, harmless request (GET /v1/models). Returns latency in ms.

    Raises on any failure. Sends no facts.
    """
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise ValueError("TYPESAFE_API_KEY is not set")
    t0 = time.perf_counter()
    r = _http().get(httpx.URL(JEV_URL).copy_with(path="/v1/models", query=None),
                    headers=_headers(key))
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    return round((time.perf_counter() - t0) * 1000)


def start_warmup(on_error: Callable[[str], None]) -> Optional[threading.Thread]:
    """In live and shadow mode, warm the connection in a background thread.

    A failure only calls on_error(text); the first real call then opens its own connection.
    """
    if JEV_MODE not in ("live", "shadow"):
        return None

    def run():
        try:
            warm_up()
        except Exception as e:  # noqa: BLE001 - any failure is only reported
            try:
                on_error(f"Jev warm-up failed ({type(e).__name__}: {e})")
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=run, name="jev-warmup", daemon=True)
    t.start()
    return t


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
            # httpx timeouts are per phase; the future enforces the total. No retry.
            data = _pool.submit(_post, request, key).result(timeout=JEV_TIMEOUT_S)
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
