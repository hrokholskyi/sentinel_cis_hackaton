"""Night circuit breaker: a proxy in front of the bank payment API.

Milestone 1: plain pass-through. Every payout is recorded as a reservation and forwarded.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

GULF = timezone(timedelta(hours=4))  # Asia/Dubai, no DST

BANK_DB = os.environ.get("BANK_DB", "bank.db")
BREAKER_DB = os.environ.get("BREAKER_DB", "breaker.db")
BANK_URL = os.environ.get("BANK_URL", "http://127.0.0.1:8001").rstrip("/")
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"

app = FastAPI(title="Night circuit breaker")


class Payout(BaseModel):
    account_id: str
    beneficiary_id: str
    amount_aed: float = Field(gt=0)
    purpose: str = ""
    basis_id: Optional[str] = None


def now_local(sim_time: Optional[str]) -> str:
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
    conn = sqlite3.connect(Path(BREAKER_DB).resolve().as_uri(), uri=True,
                           isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("ATTACH DATABASE ? AS bank", (Path(BANK_DB).resolve().as_uri() + "?mode=ro",))
    return conn


@app.get("/health")
def health():
    conn = connect()
    try:
        conn.execute("SELECT 1 FROM bank.accounts LIMIT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "service": "breaker"}


@app.post("/payouts")
def payout(
    body: Payout,
    x_api_key: Optional[str] = Header(default=None),
    x_sim_time: Optional[str] = Header(default=None),
):
    now = now_local(x_sim_time)
    conn = connect()
    try:
        key = conn.execute("SELECT id FROM bank.api_keys WHERE secret = ?",
                           (x_api_key or "",)).fetchone()
        if key is None:
            raise HTTPException(401, "invalid API key")
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] + 1
        res_id = f"res_{n:06d}"
        conn.execute(
            "INSERT INTO reservations (id, api_key_id, account_id, beneficiary_id, amount_aed,"
            " purpose, basis_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (res_id, key["id"], body.account_id, body.beneficiary_id, body.amount_aed,
             body.purpose, body.basis_id, now),
        )
        conn.execute("COMMIT")

        headers = {"X-API-Key": x_api_key}
        if DEMO_MODE and x_sim_time:
            headers["X-Sim-Time"] = x_sim_time
        r = httpx.post(f"{BANK_URL}/payouts", json=body.model_dump(exclude_none=True),
                       headers=headers, timeout=10.0)
        data = r.json()
        status = "executed" if r.status_code == 200 else "rejected"
        conn.execute(
            "UPDATE reservations SET status = ?, payment_id = ?, decided_at = ?, decided_by = 'bank'"
            " WHERE id = ?",
            (status, data.get("payment_id"), now, res_id),
        )
        data["reservation_id"] = res_id
        return JSONResponse(data, status_code=r.status_code)
    finally:
        conn.close()
