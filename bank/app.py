"""Toy bank payment API with an intentional gap.

POST /payouts checks only: the key is valid, the account belongs to the key's client,
the beneficiary is approved, the balance is enough. It never looks at history, time,
bursts or bases. That is the breaker's job.
"""

import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from scripts.common import DEV_SIM_TOKEN

GULF = timezone(timedelta(hours=4))  # Asia/Dubai, no DST

BANK_DB = os.environ.get("BANK_DB", "bank.db")
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
SIM_TOKEN = os.environ.get("SIM_TOKEN") or DEV_SIM_TOKEN

app = FastAPI(title="Toy bank")


class Payout(BaseModel):
    account_id: str
    beneficiary_id: str
    amount_aed: float = Field(gt=0)
    purpose: str = ""
    basis_id: Optional[str] = None


def now_local(sim_time: Optional[str], sim_token: Optional[str] = None) -> str:
    """Current Gulf time as 'YYYY-MM-DD HH:MM:SS'. X-Sim-Time wins in demo mode, and only
    with X-Sim-Token equal to SIM_TOKEN; otherwise the header is ignored."""
    valid = bool(sim_token) and hmac.compare_digest(sim_token.encode(), SIM_TOKEN.encode())
    if DEMO_MODE and sim_time and valid:
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
    conn = sqlite3.connect(BANK_DB, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def num(x):
    return int(x) if float(x).is_integer() else x


@app.get("/health")
def health():
    conn = connect()
    try:
        conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "service": "bank"}


@app.post("/payouts")
def payout(
    body: Payout,
    x_api_key: Optional[str] = Header(default=None),
    x_sim_time: Optional[str] = Header(default=None),
    x_sim_token: Optional[str] = Header(default=None),
):
    now = now_local(x_sim_time, x_sim_token)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            key = conn.execute(
                "SELECT id, client_id FROM api_keys WHERE secret = ?", (x_api_key or "",)
            ).fetchone()
            if key is None:
                raise HTTPException(401, "invalid API key")
            acc = conn.execute(
                "SELECT id, client_id, balance_aed FROM accounts WHERE id = ?", (body.account_id,)
            ).fetchone()
            if acc is None or acc["client_id"] != key["client_id"]:
                raise HTTPException(403, "account does not belong to this client")
            ben = conn.execute(
                "SELECT id FROM beneficiaries WHERE id = ? AND client_id = ? AND approved_at IS NOT NULL",
                (body.beneficiary_id, key["client_id"]),
            ).fetchone()
            if ben is None:
                raise HTTPException(422, "beneficiary is not approved")
            if acc["balance_aed"] < body.amount_aed:
                raise HTTPException(422, "insufficient funds")

            n = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] + 1
            payment_id = f"pay_{n:06d}"
            conn.execute(
                "UPDATE accounts SET balance_aed = balance_aed - ? WHERE id = ?",
                (body.amount_aed, body.account_id),
            )
            conn.execute(
                "INSERT INTO payments (id, account_id, beneficiary_id, api_key_id, amount_aed,"
                " purpose, basis_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (payment_id, body.account_id, body.beneficiary_id, key["id"], body.amount_aed,
                 body.purpose, body.basis_id, now),
            )
            if body.basis_id:
                # Bookkeeping only: the bank does not check bases.
                conn.execute(
                    "UPDATE bases SET used = 1 WHERE id = ? AND client_id = ?",
                    (body.basis_id, key["client_id"]),
                )
            balance = conn.execute(
                "SELECT balance_aed FROM accounts WHERE id = ?", (body.account_id,)
            ).fetchone()[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {
        "status": "executed",
        "payment_id": payment_id,
        "account_id": body.account_id,
        "beneficiary_id": body.beneficiary_id,
        "amount_aed": num(body.amount_aed),
        "balance_aed": num(balance),
        "created_at": now,
    }
