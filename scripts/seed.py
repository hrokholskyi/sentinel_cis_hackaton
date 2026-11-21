"""Create and fill bank.db and breaker.db from scratch (deterministic, idempotent).

Honours BANK_DB and BREAKER_DB so tests and replays can build databases in a temp dir.
"""

import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = 7341
TS = "%Y-%m-%d %H:%M:%S"


def fresh(path: str, schema: Path) -> sqlite3.Connection:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    conn = sqlite3.connect(path)
    conn.executescript(schema.read_text())
    return conn


def iban(n: int) -> str:
    return f"AE00{n:019d}"  # synthetic, not a valid IBAN


def at(day, hour, minute, second=0) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, second).strftime(TS)


def seed_bank(path: str) -> None:
    rng = random.Random(SEED)
    conn = fresh(path, ROOT / "bank" / "schema.sql")
    first_day = c.HISTORY_END - timedelta(days=c.HISTORY_DAYS - 1)

    conn.execute("INSERT INTO clients VALUES (?, ?)", (c.CLIENT_ID, c.CLIENT_NAME))
    conn.execute("INSERT INTO accounts VALUES (?, ?, ?)",
                 (c.ACCOUNT_ID, c.CLIENT_ID, c.OPENING_BALANCE_AED))
    conn.execute("INSERT INTO api_keys VALUES (?, ?, ?, ?)",
                 (c.API_KEY_ID, c.CLIENT_ID, c.API_KEY_SECRET, "2025-06-01 10:00:00"))

    bens, bases, payments = [], [], []
    n_iban = 1

    # Regular suppliers with daytime history and one used invoice basis per payment.
    invoice_no = 1001
    for ben_id, name, service, lo, hi, count in c.REGULARS:
        approved = c.DEMO_DATE - timedelta(days=400 + rng.randint(0, 120))
        bens.append((ben_id, c.CLIENT_ID, name, iban(n_iban), "finance.ops", at(approved, 11, 0)))
        n_iban += 1
        last_day = c.SOFT_LAST_PAID if ben_id == c.SOFT_BENEFICIARY_ID else c.HISTORY_END
        span = (last_day - first_day).days
        days = sorted(rng.sample(range(span), count - 1)) + [span]
        amounts = [lo, hi] + [c.round50(rng.uniform(lo, hi)) for _ in range(count - 2)]
        rng.shuffle(amounts)
        for d, amount in zip(days, amounts):
            day = first_day + timedelta(days=d)
            basis_id = f"inv_{invoice_no}"
            bases.append((basis_id, c.CLIENT_ID, ben_id, "invoice", amount, 1, 1,
                          c.invoice_description(invoice_no, name, service, day.strftime("%B %Y"))))
            payments.append([None, c.ACCOUNT_ID, ben_id, c.API_KEY_ID, amount,
                             f"Invoice {invoice_no}, {service}, {day.strftime('%B')}", basis_id,
                             at(day, rng.randint(9, 17), rng.randint(0, 59), rng.randint(0, 59))])
            invoice_no += 1

    # Approved, unused bases for the legit traffic plan.
    for _ts, body in c.LEGIT_PLAN:
        bases.append((body["basis_id"], c.CLIENT_ID, body["beneficiary_id"], "invoice",
                      body["amount_aed"], 1, 0, c.LEGIT_BASIS_DESCRIPTIONS[body["basis_id"]]))

    # Soft scenario basis (milestone 3), never used by legit traffic.
    bases.append((c.SOFT_BASIS_ID, c.CLIENT_ID, c.SOFT_BENEFICIARY_ID, "invoice",
                  c.SOFT_AMOUNT_AED, 1, 0, c.SOFT_BASIS_DESCRIPTION))

    # New suppliers approved 2 days ago, no payments, one approved basis each (in the plan).
    new_approved = c.DEMO_DATE - timedelta(days=2)
    for ben_id, name, _service, _amount in c.NEW_SUPPLIERS:
        bens.append((ben_id, c.CLIENT_ID, name, iban(n_iban), "finance.ops", at(new_approved, 10, 30)))
        n_iban += 1

    # Dormant beneficiaries: approved long ago by j.doe, never paid, no bases.
    for ben_id, name in c.DORMANT:
        bens.append((ben_id, c.CLIENT_ID, name, iban(n_iban), "j.doe", at(c.DORMANT_APPROVED, 16, 45)))
        n_iban += 1

    # Payroll set (milestone 6): monthly salaries on the 25th, plus this month's bases.
    pay_days = []
    day = first_day
    while day <= c.HISTORY_END:
        if day.day == 25:
            pay_days.append(day)
        day += timedelta(days=1)
    this_month = c.DEMO_DATE.strftime("%Y_%m")
    for i, ben_id in enumerate(c.EMPLOYEE_IDS):
        salary = c.round50(rng.uniform(6000, 18000))
        approved = c.DEMO_DATE - timedelta(days=300 + i)
        bens.append((ben_id, c.CLIENT_ID, f"Staff Member {i + 1:02d}", iban(n_iban),
                     "hr.payroll", at(approved, 9, 30)))
        n_iban += 1
        for pd in pay_days:
            basis_id = f"pr_{pd.strftime('%Y_%m')}_{ben_id}"
            bases.append((basis_id, c.CLIENT_ID, ben_id, "payroll", salary, 1, 1,
                          f"Salary {pd.strftime('%B %Y')}, Staff Member {i + 1:02d}"))
            payments.append([None, c.ACCOUNT_ID, ben_id, c.API_KEY_ID, salary,
                             f"Salary {pd.strftime('%B %Y')}", basis_id, at(pd, 9, 0, 2 * i)])
        bases.append((f"pr_{this_month}_{ben_id}", c.CLIENT_ID, ben_id, "payroll", salary, 1, 0,
                      f"Salary {c.DEMO_DATE.strftime('%B %Y')}, Staff Member {i + 1:02d}"))

    payments.sort(key=lambda p: (p[7], p[2]))
    for n, p in enumerate(payments, start=1):
        p[0] = f"pay_{n:06d}"

    conn.executemany("INSERT INTO beneficiaries VALUES (?, ?, ?, ?, ?, ?)", bens)
    conn.executemany("INSERT INTO bases VALUES (?, ?, ?, ?, ?, ?, ?, ?)", bases)
    conn.executemany("INSERT INTO payments (id, account_id, beneficiary_id, api_key_id,"
                     " amount_aed, purpose, basis_id, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", payments)
    conn.commit()
    conn.close()


def seed_breaker(path: str) -> None:
    conn = fresh(path, ROOT / "breaker" / "schema.sql")
    conn.execute("INSERT INTO key_state (api_key_id, state, since) VALUES (?, 'normal', ?)",
                 (c.API_KEY_ID, "2025-06-01 10:00:00"))
    conn.commit()
    conn.close()


def main() -> None:
    bank_db = os.environ.get("BANK_DB", "bank.db")
    breaker_db = os.environ.get("BREAKER_DB", "breaker.db")
    seed_bank(bank_db)
    seed_breaker(breaker_db)
    conn = sqlite3.connect(bank_db)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("beneficiaries", "bases", "payments")}
    conn.close()
    print(f"seeded {bank_db} ({counts['beneficiaries']} beneficiaries, {counts['bases']} bases, "
          f"{counts['payments']} payments) and {breaker_db}")


if __name__ == "__main__":
    main()
