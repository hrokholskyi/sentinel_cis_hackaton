"""Night attack on DEMO_DATE from 02:00 sim time, to the 3 dormant beneficiaries, no bases.

Phase A: 6 x AED 4,500, one every 30 seconds (below every threshold, executes).
Phase B from 02:03:00: 36 x AED 11,750, one every 2 seconds.
Expected: 6 executed, payments 7-8 held by soft rules, payment 9 trips burst_count_5m and
pauses the key, payments 10-42 held with reason key_paused. 36 held in total.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(results) -> list[str]:
    """Compare per-request outcomes with SPEC section 8. Returns a list of problems."""
    problems = []
    for i, (status, code, data) in enumerate(results, start=1):
        if i <= 6:
            want = ("executed", None, None)
        elif i <= 8:
            want = ("held", "soft_stop", "soft")
        elif i == 9:
            want = ("held", "hard_stop", "burst_count_5m")
        else:
            want = ("held", "key_paused", "key_paused")
        got_reason = data.get("reason") if status == "held" else None
        got_rule = data.get("level") if i in (7, 8) else data.get("rule_id")
        got = (status, got_reason, got_rule if status == "held" else None)
        if got != want:
            problems.append(f"#{i}: got {got} ({code}), want {want}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=0.2, help="real seconds between requests")
    ap.add_argument("--trace", default=os.path.join(ROOT, "traces", "attack.jsonl"),
                    help="JSONL trace to (re)write")
    ap.add_argument("--url", default=None, help="breaker URL (default: BREAKER_URL)")
    args = ap.parse_args()

    results = c.send_all(c.ATTACK_PLAN, args.trace, args.delay, args.url)
    executed = sum(1 for s, _, _ in results if s == "executed")
    held = sum(1 for s, _, _ in results if s == "held")
    print(f"sent {len(results)} executed {executed} held {held}")
    problems = check(results)
    if len(results) != 42:
        problems.append(f"sent {len(results)}, want 42")
    for p in problems:
        print("  " + p, file=sys.stderr)
    if problems:
        print("FAIL: expected 6 executed, 36 held (7-8 soft, 9 burst_count_5m, 10-42 key_paused)",
              file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
