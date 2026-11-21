"""Payroll run (milestone 6): 30 salary payments on the 25th of DEMO_DATE's month at 09:00
sim time, one every 2 seconds (09:00:00 to 09:00:58), to ben_emp_01..ben_emp_30, each with
its approved payroll basis for this month.

Every payment is legitimate: salary history on the 25th of each month, exact amount, an
approved unused basis. Still, the 9th payment (09:00:16, ben_emp_09) is the 9th reservation of
the key in 5 minutes and trips the hard rule burst_count_5m. The key is paused and an
incident opens. Expected: payments 1-8 executed, 9 held (hard_stop, burst_count_5m),
10-30 held (key_paused). 8 executed, 22 held.

Then `scripts/apply_verdict.sh <incident_id> fixtures/verdicts/payroll_release.json` re-checks
the 22 held payments and sends them all: 30 executed, key normal, incident closed.

The salaries are the client's payroll file; this script reads them from the approved payroll
bases in BANK_DB (read-only). Run it on a fresh seed (legit traffic before it is fine).
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIP_AT = 9  # 1-based position of the payment that trips burst_count_5m


def salaries(bank_db: str) -> dict:
    """Beneficiary id -> amount of this month's approved payroll basis."""
    conn = sqlite3.connect(Path(bank_db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT beneficiary_id, amount_aed FROM bases WHERE type = 'payroll'"
                            " AND approved = 1 AND id = 'pr_' || ? || '_' || beneficiary_id",
                            (c.PAYROLL_MONTH,)).fetchall()
    finally:
        conn.close()
    out = {ben: int(a) if float(a).is_integer() else a for ben, a in rows}
    missing = [b for b in c.EMPLOYEE_IDS if b not in out]
    if missing:
        raise SystemExit(f"no payroll basis for {len(missing)} employees in {bank_db}"
                         f" (first: {missing[0]}); run scripts/seed.py")
    return out


def check(results) -> list[str]:
    """Compare per-request outcomes with the expected payroll burst. Returns problems."""
    problems = []
    for i, (status, code, data) in enumerate(results, start=1):
        if i < TRIP_AT:
            want = ("executed", None, None)
        elif i == TRIP_AT:
            want = ("held", "hard_stop", "burst_count_5m")
        else:
            want = ("held", "key_paused", "key_paused")
        got = (status, data.get("reason") if status == "held" else None,
               data.get("rule_id") if status == "held" else None)
        if got != want:
            problems.append(f"#{i}: got {got} ({code}), want {want}")
    return problems


def open_incident(url: str):
    import httpx

    try:
        r = httpx.get(url.rstrip("/") + "/state", timeout=10)
        return (r.json().get("open_incident") or {}).get("id")
    except (httpx.HTTPError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delay", type=float, default=0.1, help="real seconds between requests")
    ap.add_argument("--trace", default=os.path.join(ROOT, "traces", "payroll.jsonl"),
                    help="JSONL trace to (re)write")
    ap.add_argument("--url", default=None, help="breaker URL (default: BREAKER_URL)")
    ap.add_argument("--bank-db", default=os.environ.get("BANK_DB", "bank.db"),
                    help="bank database with the payroll bases (default: BANK_DB)")
    args = ap.parse_args()

    plan = c.payroll_plan(salaries(args.bank_db))
    results = c.send_all(plan, args.trace, args.delay, args.url)
    executed = sum(1 for s, _, _ in results if s == "executed")
    held = sum(1 for s, _, _ in results if s == "held")
    print(f"sent {len(results)} executed {executed} held {held}")
    problems = check(results)
    if len(results) != len(c.EMPLOYEE_IDS):
        problems.append(f"sent {len(results)}, want {len(c.EMPLOYEE_IDS)}")
    for p in problems:
        print("  " + p, file=sys.stderr)
    if problems:
        print(f"FAIL: expected {TRIP_AT - 1} executed, {len(plan) - TRIP_AT + 1} held"
              f" ({TRIP_AT} burst_count_5m, {TRIP_AT + 1}-{len(plan)} key_paused);"
              f" run on a fresh seed", file=sys.stderr)
        return 1
    inc = open_incident(args.url or os.environ.get("BREAKER_URL", c.BREAKER_URL))
    if inc:
        print(f"incident {inc} open; release it with:"
              f" scripts/apply_verdict.sh {inc} fixtures/verdicts/payroll_release.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
