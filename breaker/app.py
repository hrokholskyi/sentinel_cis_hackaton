"""Night circuit breaker: a proxy in front of the bank payment API.

Pipeline for one payout (SPEC section 6):
1. Authenticate the key against bank.db.
2. In one BEGIN IMMEDIATE transaction: if the key is paused, hold. Otherwise insert the
   reservation as pending, evaluate all rules (counters include this payment), and mark it
   held on any violation. A hard violation pauses the key and opens an incident.
3. No violation: forward to the bank. Only soft violations: decide_soft() picks release or hold.
"""

import hmac
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from breaker import engine, incidents

GULF = timezone(timedelta(hours=4))  # Asia/Dubai, no DST

BANK_DB = os.environ.get("BANK_DB", "bank.db")
BREAKER_DB = os.environ.get("BREAKER_DB", "breaker.db")
BANK_URL = os.environ.get("BANK_URL", "http://127.0.0.1:8001").rstrip("/")
RULES_DIR = os.environ.get("RULES_DIR", "rules")
INCIDENTS_DIR = os.environ.get("INCIDENTS_DIR", "fixtures/incidents")
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
SOFT_MIN_AMOUNT_AED = float(os.environ.get("SOFT_MIN_AMOUNT_AED", "5000"))
HOLD_TTL_MIN = int(os.environ.get("HOLD_TTL_MIN", "30"))  # used from milestone 6

# Config values bound as extra named params, so thresholds stay in config.
RULE_CONFIG = {"soft_min_amount_aed": SOFT_MIN_AMOUNT_AED}

RULES = engine.load_rules(RULES_DIR)

app = FastAPI(title="Night circuit breaker")


class Payout(BaseModel):
    account_id: str
    beneficiary_id: str
    amount_aed: float = Field(gt=0)
    purpose: str = ""
    basis_id: Optional[str] = None


def now_local(sim_time: Optional[str]) -> str:
    """Current Gulf time as 'YYYY-MM-DD HH:MM:SS'. X-Sim-Time wins in demo mode."""
    if DEMO_MODE and sim_time:
        try:
            ts = datetime.fromisoformat(sim_time)
        except ValueError:
            raise HTTPException(400, "invalid X-Sim-Time")
        if ts.tzinfo is not None:
            ts = ts.astimezone(GULF).replace(tzinfo=None)
    else:
        ts = datetime.now(GULF).replace(tzinfo=None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    """One connection per request; bank.db attached read-only as schema `bank`."""
    conn = sqlite3.connect(Path(BREAKER_DB).resolve().as_uri(), uri=True,
                           isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("ATTACH DATABASE ? AS bank", (Path(BANK_DB).resolve().as_uri() + "?mode=ro",))
    return conn


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(401, "invalid admin token")


def held(res_id: str, reason: str, rule_id: Optional[str], level: Optional[str],
         detail: Optional[str] = None, code: int = 202) -> JSONResponse:
    return JSONResponse({"status": "held", "reason": reason, "reservation_id": res_id,
                         "rule_id": rule_id, "level": level, "detail": detail}, status_code=code)


def forward(conn, res, secret: str, sim_time: Optional[str], now: str,
            decided_by: str) -> JSONResponse:
    """Send a reservation to the bank and record the outcome. Call outside any transaction."""
    body = {"account_id": res["account_id"], "beneficiary_id": res["beneficiary_id"],
            "amount_aed": res["amount_aed"], "purpose": res["purpose"]}
    if res["basis_id"]:
        body["basis_id"] = res["basis_id"]
    headers = {"X-API-Key": secret}
    if DEMO_MODE and sim_time:
        headers["X-Sim-Time"] = sim_time
    try:
        r = httpx.post(f"{BANK_URL}/payouts", json=body, headers=headers, timeout=10.0)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        # Unclear outcome: keep it on hold (fail closed).
        engine.set_status(conn, res["id"], "held", now, "breaker", rule_id="bank_unavailable",
                          level="hard", detail=f"bank error: {e}")
        return held(res["id"], "bank_unavailable", "bank_unavailable", "hard", str(e), code=502)
    if r.status_code == 200:
        engine.set_status(conn, res["id"], "executed", now, decided_by,
                          payment_id=data.get("payment_id"))
    else:
        engine.set_status(conn, res["id"], "rejected", now, "bank",
                          detail=f"bank: {data.get('detail')}")
    if isinstance(data, dict):
        data["reservation_id"] = res["id"]
    return JSONResponse(data, status_code=r.status_code)


def decide_soft(conn, res, violations: list[engine.Result], now: str) -> bool:
    """Decision for a payment with only soft violations: True releases it, False keeps the hold.

    This is the single place where layer 2 (Jev, milestone 3) plugs in.
    Until then every soft stop stays held (fail closed).
    """
    return False


@app.get("/health")
def health():
    conn = connect()
    try:
        conn.execute("SELECT 1 FROM bank.accounts LIMIT 1").fetchone()
        conn.execute("SELECT 1 FROM reservations LIMIT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "service": "breaker", "rules": [r.id for r in RULES]}


@app.post("/payouts")
def payout(
    body: Payout,
    x_api_key: Optional[str] = Header(default=None),
    x_sim_time: Optional[str] = Header(default=None),
):
    t0 = time.perf_counter()
    now = now_local(x_sim_time)
    req = body.model_dump()
    conn = connect()
    try:
        key = conn.execute("SELECT id FROM bank.api_keys WHERE secret = ?",
                           (x_api_key or "",)).fetchone()
        if key is None:
            raise HTTPException(401, "invalid API key")
        key_id = key["id"]

        conn.execute("BEGIN IMMEDIATE")
        try:
            state = engine.key_state(conn, key_id, now)
            if state["state"] == "paused":
                res_id = engine.insert_reservation(
                    conn, api_key_id=key_id, body=req, status="held", now=now,
                    rule_id="key_paused", level="hard",
                    detail=f"key paused ({state['incident_id']})")
                inc = incidents.open_incident(conn, key_id)
                if inc:
                    incidents.append(conn, inc, engine.get_reservation(conn, res_id), now)
                conn.execute("COMMIT")
                return held(res_id, "key_paused", "key_paused", "hard")

            res_id = engine.insert_reservation(conn, api_key_id=key_id, body=req,
                                               status="pending", now=now)
            params = {"api_key_id": key_id, "account_id": req["account_id"],
                      "beneficiary_id": req["beneficiary_id"], "amount_aed": req["amount_aed"],
                      "now": now, "basis_id": req["basis_id"], **RULE_CONFIG}
            violations = [r for r in engine.evaluate(conn, RULES, params) if r.violated]
            chosen = engine.pick(violations)
            inc = incidents.open_incident(conn, key_id)
            if chosen:
                engine.set_status(conn, res_id, "held", now, "rules", rule_id=chosen.rule_id,
                                  level=chosen.level, detail=chosen.detail)
            if chosen and chosen.level == "hard":
                if inc is None:
                    inc = incidents.create(conn, api_key_id=key_id, rule_id=chosen.rule_id,
                                           now=now, processing_ms=(time.perf_counter() - t0) * 1000,
                                           incidents_dir=INCIDENTS_DIR)
                    engine.add_event(conn, now, "incident",
                                     f"incident {inc['id']} opened: {chosen.rule_id} ({chosen.detail})")
                else:
                    incidents.append(conn, inc, engine.get_reservation(conn, res_id), now)
                engine.set_key_state(conn, key_id, "paused", now, inc["id"])
                engine.add_event(conn, now, "pause", f"key {key_id} paused by {chosen.rule_id}")
            elif inc:
                incidents.append(conn, inc, engine.get_reservation(conn, res_id), now)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        res = engine.get_reservation(conn, res_id)
        if chosen and chosen.level == "hard":
            return held(res_id, "hard_stop", chosen.rule_id, chosen.level, chosen.detail)
        if chosen:
            if decide_soft(conn, res, violations, now):
                engine.set_status(conn, res_id, "released", now, "jev")
                return forward(conn, engine.get_reservation(conn, res_id), x_api_key,
                               x_sim_time, now, "jev")
            engine.add_event(conn, now, "notify",
                             f"client asked to confirm payment {res_id}: AED "
                             f"{incidents.num(res['amount_aed'])} to {res['beneficiary_id']}")
            return held(res_id, "soft_stop", chosen.rule_id, chosen.level, chosen.detail)
        return forward(conn, res, x_api_key, x_sim_time, now, "breaker")
    finally:
        conn.close()


# --- admin ------------------------------------------------------------------

@app.post("/admin/keys/{key_id}/resume", dependencies=[Depends(require_admin)])
def resume_key(key_id: str, x_sim_time: Optional[str] = Header(default=None)):
    now = now_local(x_sim_time)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM key_state WHERE api_key_id = ?", (key_id,)).fetchone() is None:
            conn.execute("ROLLBACK")
            raise HTTPException(404, "unknown key")
        conn.execute("UPDATE incidents SET status = 'closed' WHERE api_key_id = ? AND status = 'open'",
                     (key_id,))
        engine.set_key_state(conn, key_id, "normal", now, None)
        engine.add_event(conn, now, "admin", f"key {key_id} resumed by admin")
        conn.execute("COMMIT")
        return {"api_key_id": key_id, "state": "normal"}
    finally:
        conn.close()


def _take_hold(conn, res_id: str, new_status: str, now: str):
    """Move a held reservation to new_status atomically; 404/409 otherwise."""
    conn.execute("BEGIN IMMEDIATE")
    res = engine.get_reservation(conn, res_id)
    if res is None or res["status"] != "held":
        conn.execute("ROLLBACK")
        if res is None:
            raise HTTPException(404, "unknown reservation")
        raise HTTPException(409, f"reservation is {res['status']}, not held")
    engine.set_status(conn, res_id, new_status, now, "admin")
    engine.add_event(conn, now, "admin", f"hold {res_id} {new_status} by admin")
    conn.execute("COMMIT")
    return engine.get_reservation(conn, res_id)


@app.post("/admin/holds/{res_id}/release", dependencies=[Depends(require_admin)])
def release_hold(res_id: str, x_sim_time: Optional[str] = Header(default=None)):
    now = now_local(x_sim_time)
    conn = connect()
    try:
        res = _take_hold(conn, res_id, "released", now)
        secret = conn.execute("SELECT secret FROM bank.api_keys WHERE id = ?",
                              (res["api_key_id"],)).fetchone()
        if secret is None:
            engine.set_status(conn, res_id, "held", now, "admin", detail="key no longer exists")
            raise HTTPException(409, "API key no longer exists")
        return forward(conn, res, secret["secret"], x_sim_time, now, "admin")
    finally:
        conn.close()


@app.post("/admin/holds/{res_id}/reject", dependencies=[Depends(require_admin)])
def reject_hold(res_id: str, x_sim_time: Optional[str] = Header(default=None)):
    now = now_local(x_sim_time)
    conn = connect()
    try:
        res = _take_hold(conn, res_id, "rejected", now)
        return {"reservation_id": res_id, "status": res["status"]}
    finally:
        conn.close()


# --- state ------------------------------------------------------------------

@app.get("/state")
def state():
    conn = connect()
    try:
        keys = [dict(r) for r in conn.execute("SELECT * FROM key_state ORDER BY api_key_id")]
        counts = {s: 0 for s in ("pending", "executed", "held", "released", "rejected")}
        amounts = dict.fromkeys(counts, 0)
        for r in conn.execute("SELECT status, COUNT(*) n, COALESCE(SUM(amount_aed), 0) s"
                              " FROM reservations GROUP BY status"):
            counts[r["status"]] = r["n"]
            amounts[r["status"]] = incidents.num(r["s"])
        incs = [dict(r) for r in conn.execute("SELECT * FROM incidents ORDER BY id")]
        open_inc = next((i for i in reversed(incs) if i["status"] == "open"), None)
        events = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 10")]
        return {"keys": keys, "reservations": counts, "reservations_aed": amounts,
                "open_incident": open_inc, "incidents": incs, "events": events}
    finally:
        conn.close()
