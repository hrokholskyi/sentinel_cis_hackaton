"""One soft case on DEMO_DATE at 02:10:00 sim time: AED 48,000 to ben_reg_05, approved invoice inv_4471.

Layer 1 holds it (off hours, amount above AED 5,000). Jev should release it, so the expected
outcome is executed. Run it after legit traffic and BEFORE the attack: after the attack the
1 hour sum rule makes the same payment a hard stop.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOFT_PLAN = [(
    datetime.combine(c.DEMO_DATE, datetime.strptime("02:10:00", "%H:%M:%S").time()),
    {
        "account_id": c.ACCOUNT_ID,
        "beneficiary_id": c.SOFT_BENEFICIARY_ID,
        "amount_aed": c.SOFT_AMOUNT_AED,
        "purpose": c.SOFT_PURPOSE,
        "basis_id": c.SOFT_BASIS_ID,
    },
)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=0.2, help="real seconds between requests")
    ap.add_argument("--trace", default=os.path.join(ROOT, "traces", "soft.jsonl"),
                    help="JSONL trace to (re)write")
    ap.add_argument("--url", default=None, help="breaker URL (default: BREAKER_URL)")
    ap.add_argument("--expect", choices=("executed", "held"), default="executed",
                    help="outcome that makes the script exit 0")
    args = ap.parse_args()

    results = c.send_all(SOFT_PLAN, args.trace, args.delay, args.url)
    status, code, data = results[0]
    print(f"sent 1 executed {int(status == 'executed')} held {int(status == 'held')}")
    if status != args.expect:
        print(f"FAIL: got {status} ({code}) {data}, want {args.expect}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
