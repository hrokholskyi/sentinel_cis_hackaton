"""Shared ids, demo calendar and traffic plans for seed.py and the traffic scripts.

Everything here is synthetic and deterministic.
"""

import json
import os
import random
from datetime import date, datetime, timedelta

DEMO_DATE = date.fromisoformat(os.environ.get("DEMO_DATE", "2026-11-21"))
LEGIT_DATE = DEMO_DATE - timedelta(days=1)
HISTORY_END = DEMO_DATE - timedelta(days=2)
HISTORY_DAYS = 90
GULF_OFFSET = "+04:00"

CLIENT_ID = "cli_microsoft"
CLIENT_NAME = "Microsoft"
ACCOUNT_ID = "acc_microsoft_1"
OPENING_BALANCE_AED = 1_500_000
API_KEY_ID = "key_microsoft_1"
API_KEY_SECRET = "demo_sk_microsoft_0f6b2c9e4d7a1358"  # synthetic, not a real credential

BREAKER_URL = os.environ.get("BREAKER_URL", "http://127.0.0.1:8000")

# Regular suppliers: (id, name, service, typical low, typical high, prior payment count).
# The history of each one contains both its low and its high amount exactly.
REGULARS = [
    ("ben_reg_01", "Saffron Freight Services", "freight forwarding", 5000, 18000, 12),
    ("ben_reg_02", "Dune Pallet Supply", "pallets and packaging", 6000, 22000, 15),
    ("ben_reg_03", "Creekside Fuel Trading", "fleet fuel", 8000, 30000, 18),
    ("ben_reg_04", "Harbor Tyre Works", "tyres and repairs", 5000, 15000, 10),
    ("ben_reg_05", "Falcon Cargo Handling", "logistics services", 30000, 52000, 14),
    ("ben_reg_06", "Oasis Office Supplies", "office supplies", 5000, 12000, 20),
    ("ben_reg_07", "Pearl Customs Brokers", "customs brokerage", 10000, 35000, 13),
    ("ben_reg_08", "Mirage Fleet Maintenance", "fleet maintenance", 7000, 26000, 16),
    ("ben_reg_09", "Coral IT Services", "IT services", 12000, 40000, 11),
    ("ben_reg_10", "Sandstone Warehousing", "warehouse rent", 6000, 20000, 17),
    ("ben_reg_11", "Lagoon Cleaning Services", "cleaning services", 5000, 14000, 19),
    ("ben_reg_12", "Palm Courier Network", "courier services", 9000, 28000, 12),
]

# Soft scenario (milestone 3): ben_reg_05 has a prior max of exactly AED 52,000 and
# its last payment 31 days before DEMO_DATE. Legit traffic never pays it.
SOFT_BENEFICIARY_ID = "ben_reg_05"
SOFT_LAST_PAID = DEMO_DATE - timedelta(days=31)
SOFT_BASIS_ID = "inv_4471"
SOFT_AMOUNT_AED = 48000
SOFT_PURPOSE = "Invoice 4471, logistics services, October"

# New suppliers, approved 2 days before DEMO_DATE: (id, name, service, first payment amount).
NEW_SUPPLIERS = [
    ("ben_new_01", "Bluewave Marine Spares", "marine spares", 3200),
    ("ben_new_02", "Amber Signage Studio", "vehicle signage", 4750),
]

# Dormant beneficiaries: approved long ago by j.doe, never paid, no bases.
DORMANT = [
    ("ben_dorm_01", "Northgate General Trading"),
    ("ben_dorm_02", "Silverline Commodities"),
    ("ben_dorm_03", "Eastport Supply Solutions"),
]
DORMANT_APPROVED = DEMO_DATE - timedelta(days=214)

EMPLOYEE_COUNT = 30
EMPLOYEE_IDS = [f"ben_emp_{i:02d}" for i in range(1, EMPLOYEE_COUNT + 1)]

LEGIT_START = "09:00:00"
LEGIT_SPACING_MIN = 13
LEGIT_MAX_AMOUNT = 25000
LEGIT_RNG_SEED = 20261120
LEGIT_NEW_SUPPLIER_SLOTS = (10, 30)  # positions of the new supplier payments in the plan


def iso(ts: datetime) -> str:
    """Gulf time naive datetime -> ISO 8601 with +04:00 for the X-Sim-Time header."""
    return ts.strftime("%Y-%m-%dT%H:%M:%S") + GULF_OFFSET


def round50(x: float) -> int:
    return int(round(x / 50.0)) * 50


def invoice_description(invoice_no: int, name: str, service: str, period: str) -> str:
    """Text of an approved invoice basis (bank.bases.description)."""
    return f"Invoice {invoice_no} from {name}: {service}, {period}"


# inv_4471 bills the month before DEMO_DATE ("October" in SOFT_PURPOSE).
SOFT_BASIS_DESCRIPTION = invoice_description(
    4471, "Falcon Cargo Handling", "logistics services",
    (DEMO_DATE.replace(day=1) - timedelta(days=1)).strftime("%B %Y"))

# basis_id -> description for the legit plan's bases, filled by _legit_plan().
LEGIT_BASIS_DESCRIPTIONS: dict = {}


def _legit_plan():
    rng = random.Random(LEGIT_RNG_SEED)
    regulars = [r for r in REGULARS if r[0] != SOFT_BENEFICIARY_ID]
    picks = (regulars * 4)[:40]
    rng.shuffle(picks)
    items = []
    invoice_no = 3001
    month = LEGIT_DATE.strftime("%B %Y")
    for ben_id, name, service, lo, hi, _n in picks:
        amount = round50(rng.uniform(lo, min(hi, LEGIT_MAX_AMOUNT)))
        basis_id = f"inv_{invoice_no}"
        LEGIT_BASIS_DESCRIPTIONS[basis_id] = invoice_description(invoice_no, name, service, month)
        items.append({
            "beneficiary_id": ben_id,
            "amount_aed": amount,
            "purpose": f"Invoice {invoice_no}, {service}, November",
            "basis_id": basis_id,
        })
        invoice_no += 1
    for slot, (ben_id, name, service, amount) in zip(LEGIT_NEW_SUPPLIER_SLOTS, NEW_SUPPLIERS):
        basis_id = f"inv_{invoice_no}"
        LEGIT_BASIS_DESCRIPTIONS[basis_id] = invoice_description(invoice_no, name, service,
                                                                 "first order")
        items.insert(slot, {
            "beneficiary_id": ben_id,
            "amount_aed": amount,
            "purpose": f"Invoice {invoice_no}, {service}, first order",
            "basis_id": basis_id,
        })
        invoice_no += 1
    start = datetime.combine(LEGIT_DATE, datetime.strptime(LEGIT_START, "%H:%M:%S").time())
    plan = []
    for i, item in enumerate(items):
        ts = start + timedelta(minutes=LEGIT_SPACING_MIN * i)
        plan.append((ts, {"account_id": ACCOUNT_ID, **item}))
    return plan


def _check_legit_budget(plan):
    total = sum(body["amount_aed"] for _, body in plan)
    assert len(plan) == 42, len(plan)
    assert total < 700_000, total
    for ts, _ in plan:
        window = [b["amount_aed"] for t, b in plan if ts - timedelta(hours=1) <= t <= ts]
        assert len(window) <= 5 and sum(window) < 150_000, (ts, window)
    for ts, _ in plan:
        assert 9 <= ts.hour < 18, ts


LEGIT_PLAN = _legit_plan()
_check_legit_budget(LEGIT_PLAN)

ATTACK_PURPOSE = "Supplier settlement, urgent"
ATTACK_PHASE_A = (6, 4500, 30)   # count, amount, seconds between payments
ATTACK_PHASE_B = (36, 11750, 2)


def _attack_plan():
    plan = []
    start_a = datetime.combine(DEMO_DATE, datetime.strptime("02:00:00", "%H:%M:%S").time())
    start_b = datetime.combine(DEMO_DATE, datetime.strptime("02:03:00", "%H:%M:%S").time())
    n = 0
    for start, (count, amount, step) in ((start_a, ATTACK_PHASE_A), (start_b, ATTACK_PHASE_B)):
        for i in range(count):
            plan.append((start + timedelta(seconds=step * i), {
                "account_id": ACCOUNT_ID,
                "beneficiary_id": DORMANT[n % len(DORMANT)][0],
                "amount_aed": amount,
                "purpose": ATTACK_PURPOSE,
            }))
            n += 1
    return plan


ATTACK_PLAN = _attack_plan()


def send_all(plan, trace_path, delay, url=None):
    """POST every planned payout to the breaker and write the JSONL trace.

    Returns the list of (status, http_code, response_json) per request.
    """
    import time

    import httpx

    url = (url or os.environ.get("BREAKER_URL", BREAKER_URL)).rstrip("/")
    results = []
    os.makedirs(os.path.dirname(os.path.abspath(trace_path)), exist_ok=True)
    with httpx.Client(base_url=url, timeout=10.0) as client, open(trace_path, "w") as trace:
        for i, (ts, body) in enumerate(plan):
            if i and delay > 0:
                time.sleep(delay)
            sim_time = iso(ts)
            r = client.post(
                "/payouts",
                json=body,
                headers={"X-API-Key": API_KEY_SECRET, "X-Sim-Time": sim_time},
            )
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text}
            if r.status_code == 200:
                status = "executed"
            elif r.status_code == 202:
                status = data.get("status", "held")
            else:
                status = "rejected"
            line = {
                "sim_time": sim_time,
                "request": body,
                "status": status,
                "reservation_id": data.get("reservation_id") if isinstance(data, dict) else None,
            }
            trace.write(json.dumps(line) + "\n")
            trace.flush()
            results.append((status, r.status_code, data))
    return results
