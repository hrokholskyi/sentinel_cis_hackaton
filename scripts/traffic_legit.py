"""Legitimate daytime traffic: 40 invoice payments to regular suppliers plus first
payments to the 2 new suppliers, on DEMO_DATE - 1 between 09:00 and 18:00 sim time.

Expected: all 42 executed, zero held.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=0.05, help="real seconds between requests")
    ap.add_argument("--trace", default=os.path.join(ROOT, "fixtures", "legit.jsonl"),
                    help="JSONL trace to (re)write")
    ap.add_argument("--url", default=None, help="breaker URL (default: BREAKER_URL)")
    args = ap.parse_args()

    results = c.send_all(c.LEGIT_PLAN, args.trace, args.delay, args.url)
    executed = sum(1 for s, _, _ in results if s == "executed")
    held = sum(1 for s, _, _ in results if s == "held")
    print(f"sent {len(results)} executed {executed} held {held}")
    ok = len(results) == 42 and executed == 42 and held == 0
    if not ok:
        for i, (s, code, data) in enumerate(results, start=1):
            if s != "executed":
                print(f"  #{i}: {s} ({code}) {data}", file=sys.stderr)
        print("FAIL: expected 42 executed, 0 held", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
