"""TrustGap: a proxy in front of the bank payment API.

Pipeline for one payout (SPEC section 6):
1. Authenticate the key against bank.db.
2. In one BEGIN IMMEDIATE transaction: if the key is paused, hold. Otherwise insert the
   reservation as pending, evaluate all rules (counters include this payment), and mark it
   held on any violation. A hard violation pauses the key and opens an incident.
3. No violation: forward to the bank. Only soft violations: decide_soft() picks release or hold.

Milestone 6: every payout, verdict and admin call first runs the lazy hold expiry of its key
(engine.expire_holds) on the request clock. A key in probe passes only payments that pass
the rules and engine.probe_gate(); after PROBE_CLEAN_PAYMENTS clean ones it is normal again.
"""

import hmac
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from breaker import dashboard, engine, incidents, jev

GULF = timezone(timedelta(hours=4))  # Asia/Dubai, no DST

BANK_DB = os.environ.get("BANK_DB", "bank.db")
BREAKER_DB = os.environ.get("BREAKER_DB", "breaker.db")
BANK_URL = os.environ.get("BANK_URL", "http://127.0.0.1:8001").rstrip("/")
RULES_DIR = os.environ.get("RULES_DIR", "rules")
INCIDENTS_DIR = os.environ.get("INCIDENTS_DIR", "fixtures/incidents")
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
SOFT_MIN_AMOUNT_AED = float(os.environ.get("SOFT_MIN_AMOUNT_AED", "5000"))
HOLD_TTL_MIN = int(os.environ.get("HOLD_TTL_MIN", "30"))
PROBE_MAX_AMOUNT_AED = float(os.environ.get("PROBE_MAX_AMOUNT_AED", "1000"))
PROBE_CLEAN_PAYMENTS = int(os.environ.get("PROBE_CLEAN_PAYMENTS", "5"))

# Config values bound as extra named params, so thresholds stay in config.
RULE_CONFIG = {"soft_min_amount_aed": SOFT_MIN_AMOUNT_AED}

RULES = engine.load_rules(RULES_DIR)

app = FastAPI(title="TrustGap")


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

    Layer 2 (Jev). Runs after the hold is committed, outside any transaction.
    Any failure in Jev or in its logging keeps the hold (fail closed).
    """
    try:
        trace: dict = {}
        result = jev.assess(jev.build_facts(conn, res, now), trace)
        released = bool(result and result.released)
        conn.execute(
            "INSERT INTO jev_calls (reservation_id, request_json, response_json, latency_ms,"
            " released, mode) VALUES (?, ?, ?, ?, ?, ?)",
            (res["id"], trace.get("request_json"), trace.get("response_json"),
             trace.get("latency_ms"), int(released), trace.get("mode")))
        if released:
            engine.add_event(conn, now, "jev",
                             f"Jev released {res['id']}: fits_history {result.fits_history:.2f}, "
                             f"purpose_matches_basis {result.purpose_matches_basis:.2f}")
        elif result is None:
            engine.add_event(conn, now, "jev",
                             f"Jev gave no answer for {res['id']} ({trace.get('mode')}), hold kept")
        else:
            engine.add_event(conn, now, "jev",
                             f"Jev kept hold {res['id']} ({result.mode}): fits_history "
                             f"{result.fits_history:.2f}, purpose_matches_basis "
                             f"{result.purpose_matches_basis:.2f}")
        return released
    except Exception as e:
        engine.add_event(conn, now, "error", f"Jev step failed for {res['id']}: {e}")
        return False


def _jev_warmup_failed(text: str) -> None:
    conn = connect()
    try:
        engine.add_event(conn, now_local(None), "jev", text)
    finally:
        conn.close()


# Live and shadow mode: open the TLS connection to Jev at start, in the background.
jev.start_warmup(_jev_warmup_failed)


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

        tripped = None  # incident created by this request, for the webhook after commit
        conn.execute("BEGIN IMMEDIATE")
        try:
            engine.expire_holds(conn, key_id, now, HOLD_TTL_MIN)
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
            probe_since = state["since"] if state["state"] == "probe" else None
            probe_why = None
            if probe_since is not None and not (chosen and chosen.level == "hard"):
                probe_why = engine.probe_gate(conn, req["beneficiary_id"], req["amount_aed"], now,
                                              PROBE_MAX_AMOUNT_AED)
            inc = incidents.open_incident(conn, key_id)
            if probe_why:
                engine.set_status(conn, res_id, "held", now, "probe", rule_id="probe",
                                  level="hard", detail=probe_why)
                if inc:
                    incidents.append(conn, inc, engine.get_reservation(conn, res_id), now)
                conn.execute("COMMIT")
                return held(res_id, "probe", "probe", "hard", probe_why)
            if chosen:
                engine.set_status(conn, res_id, "held", now, "rules", rule_id=chosen.rule_id,
                                  level=chosen.level, detail=chosen.detail)
            if chosen and chosen.level == "hard":
                if inc is None:
                    inc = incidents.create(conn, api_key_id=key_id, rule_id=chosen.rule_id,
                                           now=now, processing_ms=(time.perf_counter() - t0) * 1000,
                                           incidents_dir=INCIDENTS_DIR)
                    tripped = inc["id"]
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
        if tripped:
            incidents.notify(connect, tripped, next((r.sql for r in RULES
                                                     if r.id == chosen.rule_id), None))

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
        if probe_since is None:
            return forward(conn, res, x_api_key, x_sim_time, now, "breaker")
        # Key in probe: a payment with no violation that the bank executes is a clean one.
        resp = forward(conn, res, x_api_key, x_sim_time, now, "probe")
        if engine.get_reservation(conn, res_id)["status"] == "executed":
            conn.execute("BEGIN IMMEDIATE")
            engine.count_probe_clean(conn, key_id, probe_since, now, PROBE_CLEAN_PAYMENTS)
            conn.execute("COMMIT")
        return resp
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
        expire_for_key(conn, key_id, x_sim_time)  # an expired incident stays expired
        conn.execute("UPDATE incidents SET status = 'closed' WHERE api_key_id = ? AND status = 'open'",
                     (key_id,))
        engine.set_key_state(conn, key_id, "normal", now, None)
        engine.add_event(conn, now, "admin", f"key {key_id} resumed by admin")
        conn.execute("COMMIT")
        return {"api_key_id": key_id, "state": "normal"}
    finally:
        conn.close()


def request_clock(conn, key_id: str, sim_time: Optional[str]) -> Optional[str]:
    """'Now' for hold expiry on an admin call: X-Sim-Time if sent, else the latest request
    time of the key (never the wall clock, which is far from the demo clock)."""
    if sim_time:
        return now_local(sim_time)
    return conn.execute("SELECT MAX(created_at) FROM reservations WHERE api_key_id = ?",
                        (key_id,)).fetchone()[0]


def expire_for_key(conn, key_id: str, sim_time: Optional[str]) -> None:
    """Run the lazy hold expiry of one key inside the caller's BEGIN IMMEDIATE."""
    now = request_clock(conn, key_id, sim_time)
    if now:
        engine.expire_holds(conn, key_id, now, HOLD_TTL_MIN)


def _take_hold(conn, res_id: str, new_status: str, now: str, sim_time: Optional[str]):
    """Move a held reservation to new_status atomically; 404/409 otherwise.

    The key's hold expiry runs first, so an expired hold answers 409 (it is rejected).
    """
    conn.execute("BEGIN IMMEDIATE")
    res = engine.get_reservation(conn, res_id)
    if res is not None and res["status"] == "held":
        expire_for_key(conn, res["api_key_id"], sim_time)
        res = engine.get_reservation(conn, res_id)
    if res is None or res["status"] != "held":
        conn.execute("COMMIT")  # keeps an expiry that just ran; nothing else was written
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
        res = _take_hold(conn, res_id, "released", now, x_sim_time)
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
        res = _take_hold(conn, res_id, "rejected", now, x_sim_time)
        return {"reservation_id": res_id, "status": res["status"]}
    finally:
        conn.close()


# --- verdicts (SPEC section 9) ----------------------------------------------

class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis_ids: list[str] = Field(default_factory=list, max_length=200)
    prior_payment_ids: list[str] = Field(default_factory=list, max_length=200)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    verdict: Literal["release", "hold_until_morning", "contact_client", "escalate_bank_security"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence: Evidence = Field(default_factory=Evidence)
    pr_url: Optional[str] = Field(default=None, max_length=500, pattern=r"^https?://\S+$")


VERDICT_LOCK = threading.Lock()  # one verdict at a time, so a basis can only release once


def _release(conn, inc, now: str, sim_time: str) -> tuple[list, list]:
    """Re-check every held payment of the incident, one at a time, and send those that pass.

    The evidence in the verdict body is never trusted: the database decides.
    """
    released, kept = [], []
    secret = conn.execute("SELECT secret FROM bank.api_keys WHERE id = ?",
                          (inc["api_key_id"],)).fetchone()
    for row in incidents.held_payments(conn, inc["id"]):
        conn.execute("BEGIN IMMEDIATE")
        res = engine.get_reservation(conn, row["id"])
        if res["status"] != "held":
            conn.execute("ROLLBACK")
            continue
        basis_id, why = incidents.recheck(conn, res) if secret else (None, "API key is gone")
        if basis_id is None:
            conn.execute("ROLLBACK")
            kept.append({"reservation_id": res["id"], "why": why})
            continue
        engine.set_status(conn, res["id"], "released", now, "verdict", basis_id=basis_id)
        conn.execute("COMMIT")
        forward(conn, engine.get_reservation(conn, res["id"]), secret["secret"], sim_time, now,
                "verdict")
        after = engine.get_reservation(conn, res["id"])
        if after["status"] == "executed":
            released.append(res["id"])
        else:
            kept.append({"reservation_id": res["id"], "why": f"{after['status']}: {after['detail']}"})
    return released, kept


@app.post("/incidents/{incident_id}/verdict", dependencies=[Depends(require_admin)])
def verdict(incident_id: str, body: Verdict, x_sim_time: Optional[str] = Header(default=None)):
    if body.incident_id != incident_id:
        raise HTTPException(422, "incident_id in the body does not match the path")
    with VERDICT_LOCK:
        conn = connect()
        try:
            inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if inc is None:
                raise HTTPException(404, "unknown incident")
            now = now_local(x_sim_time) if x_sim_time else incidents.request_clock(conn, inc)
            if inc["status"] == "open":  # lazy hold expiry of the key, on the verdict's clock
                conn.execute("BEGIN IMMEDIATE")
                engine.expire_holds(conn, inc["api_key_id"], now, HOLD_TTL_MIN)
                conn.execute("COMMIT")
                inc = conn.execute("SELECT * FROM incidents WHERE id = ?",
                                   (incident_id,)).fetchone()
            if inc["status"] == "expired":
                raise HTTPException(409, "incident is expired: its holds were rejected when the"
                                         " hold limit passed, nothing left to decide")
            if inc["status"] != "open":
                raise HTTPException(409, f"incident is {inc['status']}")
            requested, final, status, hold_until = body.verdict, body.verdict, "open", None
            released, kept = [], []
            notes = []
            if requested == "release":
                released, kept = _release(conn, inc, now, incidents.iso_local(now))
                if kept:
                    final = "contact_client"
                    notes.append(("verdict", f"release verdict for {incident_id} downgraded to "
                                  f"contact_client: {len(released)} sent, {len(kept)} still held"))
                    notes.append(("notify", f"client contacted by second channel (simulated) "
                                  f"for {incident_id}"))
                else:
                    status = "closed"
                    notes.append(("verdict", f"release verdict for {incident_id}: all "
                                  f"{len(released)} held payments re-checked and sent"))
            elif requested == "hold_until_morning":
                hold_until = incidents.next_morning(now)
                notes.append(("verdict", f"holds of {incident_id} extended until {hold_until}"))
            elif requested == "contact_client":
                notes.append(("notify", f"client contacted by second channel (simulated) "
                              f"for {incident_id}"))
            else:
                notes.append(("notify", f"bank security paged (simulated) for {incident_id}"))

            conn.execute("BEGIN IMMEDIATE")
            incidents.record_verdict(conn, incident_id, requested=requested, verdict=final,
                                     reason=body.reason, evidence=body.evidence.model_dump(),
                                     pr_url=body.pr_url, now=now, status=status,
                                     hold_until=hold_until)
            if status == "closed":
                engine.set_key_state(conn, inc["api_key_id"], "normal", now, None)
                notes.append(("admin", f"key {inc['api_key_id']} back to normal"))
            engine.add_event(conn, now, "verdict", f"verdict {requested} received for {incident_id}")
            for kind, text in notes:
                engine.add_event(conn, now, kind, text)
            conn.execute("COMMIT")
            key = engine.key_state(conn, inc["api_key_id"], now)
            out = dict(conn.execute("SELECT * FROM incidents WHERE id = ?",
                                    (incident_id,)).fetchone())
            return {"incident_id": incident_id, "requested_verdict": requested, "verdict": final,
                    "status": out["status"], "key_state": key["state"],
                    "hold_until": out["hold_until"], "released": released, "still_held": kept,
                    "held_count": len(incidents.held_payments(conn, incident_id))}
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
        for i in incs:
            i["pause_to_pr_min"] = incidents.pause_to_pr_min(i)
        open_inc = next((i for i in reversed(incs) if i["status"] == "open"), None)
        events = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 10")]
        out = {"keys": keys, "reservations": counts, "reservations_aed": amounts,
               "open_incident": open_inc, "incidents": incs, "events": events}
        out.update(dashboard.build(conn, incs=incs, keys=keys, rules_loaded=len(RULES),
                                   probe_target=PROBE_CLEAN_PAYMENTS))
        return out
    finally:
        conn.close()


# --- dashboard and presenter mode -------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html",
                        headers={"Cache-Control": "no-store"})


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kicker: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=80)


class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=120)
    text: str = Field(max_length=8000)


class Presenter(BaseModel):
    """All fields optional: a missing field keeps its value, null clears it."""
    model_config = ConfigDict(extra="forbid")
    caption: Optional[str] = Field(default=None, max_length=400)
    spotlight: Optional[Literal[dashboard.SPOTLIGHTS]] = None
    card: Optional[Card] = None
    step: Optional[int] = Field(default=None, ge=0, le=5)
    step_done: Optional[list[Annotated[int, Field(ge=0, le=5)]]] = Field(default=None,
                                                                         max_length=6)
    panel: Optional[Panel] = None


@app.post("/admin/presenter", dependencies=[Depends(require_admin)])
def set_presenter(body: Presenter):
    return dashboard.presenter_update({f: (v.model_dump() if isinstance(v, BaseModel) else v)
                                       for f in body.model_fields_set
                                       for v in [getattr(body, f)]})


@app.delete("/admin/presenter", dependencies=[Depends(require_admin)])
def clear_presenter():
    return dashboard.presenter_clear()
