"""Incident record and trace file.

On the first hard violation for a key an incident is created and the key's requests from
the last hour are written to <INCIDENTS_DIR>/<incident_id>.jsonl. Every later request from
that key is appended while the incident is open.

All functions run inside the caller's BEGIN IMMEDIATE transaction, so the trace file is
written in the same order as the reservations are committed.
"""

import json
import os
from datetime import datetime
from typing import Optional

TS = "%Y-%m-%d %H:%M:%S"


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
        " violation_to_pause_ms, trace_file) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
        (incident_id, api_key_id, rule_id, now, first, round(gap_ms + processing_ms), trace_file),
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
    """Append one later request of the key to the open incident's trace file."""
    try:
        with open(incident["trace_file"], "a") as f:
            f.write(json.dumps(trace_line(res)) + "\n")
    except OSError as e:
        conn.execute("INSERT INTO events (at, kind, text) VALUES (?, 'error', ?)",
                     (now, f"trace file {incident['trace_file']} not appended: {e}"))
