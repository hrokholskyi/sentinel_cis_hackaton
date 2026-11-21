"""Was our own infrastructure changed? Run through scripts/infra_check.sh.

(a) commits of the last 24 hours (real time) in monitored paths, COVERED or NOT COVERED
(b) uncommitted changes in monitored paths
(c) rule file checksums against rules/rules.lock
(d) API keys created in the 24 hours before the latest incident (request clock)
(e) breaker /health at BREAKER_URL (not reachable is only a note)

Exit 1 if a commit is NOT COVERED, (b) is not empty, (c) differs, or a key in (d) is not
named in CHANGELOG.md. Otherwise print "infra check: clean" and exit 0.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import httpx

import changelog as cl
import common
import replaylib


def section(title: str) -> None:
    print(f"\n{title}")


def recent_keys() -> tuple[str, str, list]:
    """(reference time, how it was chosen, [(key id, created_at)])."""
    bank_db, breaker_db = replaylib.resolve_databases()
    conn = sqlite3.connect(bank_db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS brk", (breaker_db.resolve().as_uri() + "?mode=ro",))
        row = conn.execute("SELECT id, tripped_at FROM brk.incidents ORDER BY id DESC LIMIT 1"
                           ).fetchone()
        if row:
            ref, how = row[1], f"tripped_at of {row[0]}, request clock"
        else:
            ref = datetime.now(timezone(timedelta(hours=4))).strftime("%Y-%m-%d %H:%M:%S")
            how = "real time now, no incident"
        keys = conn.execute("SELECT id, created_at FROM api_keys"
                            " WHERE created_at > datetime(?, '-24 hours') AND created_at <= ?"
                            " ORDER BY created_at", (ref, ref)).fetchall()
    finally:
        conn.close()
    return ref, how, keys


def main() -> int:
    problems = []
    text = cl.changelog_text()
    try:
        section("(a) commits in the last 24 hours touching " + ", ".join(cl.MONITORED)
                + " (not rules/rules.lock)")
        recent = cl.commits(since="24 hours ago")
        for c in recent:
            ok = cl.covered(c["hash"], text)
            print(f"  {c['short']}  {c['date']}  {'COVERED    ' if ok else 'NOT COVERED'}  {c['subject']}")
            if not ok:
                problems.append(f"commit {c['short']} has no CHANGELOG.md entry")
        if not recent:
            print("  (none)")

        section("(b) uncommitted changes in monitored paths")
        dirty = cl.uncommitted()
        for line in dirty:
            print("  " + line)
        if dirty:
            problems.append(f"{len(dirty)} uncommitted changes in monitored paths")
        else:
            print("  (none)")
    except cl.GitError as e:
        print(f"  error: {e}")
        problems.append("git history not readable")

    section("(c) rule files against rules/rules.lock")
    diff = cl.lock_diff()
    for line in diff:
        print("  " + line)
    if diff:
        problems.append(f"{len(diff)} rule file differences against rules/rules.lock")
    else:
        print(f"  {len(cl.rule_checksums())} rule files match rules/rules.lock")

    ref, how, keys = recent_keys()
    section(f"(d) API keys created in the 24 hours before {ref} ({how})")
    for key_id, created in keys:
        named = key_id in text
        print(f"  {key_id}  {created}  {'in CHANGELOG.md' if named else 'NOT in CHANGELOG.md'}")
        if not named:
            problems.append(f"API key {key_id} is not named in CHANGELOG.md")
    if not keys:
        print("  (none)")

    url = os.environ.get("BREAKER_URL", common.BREAKER_URL).rstrip("/")
    section(f"(e) breaker health at {url}")
    try:
        r = httpx.get(url + "/health", timeout=2.0)
        print(f"  HTTP {r.status_code} {r.text.strip()[:300]}")
    except httpx.HTTPError as e:
        print(f"  NOTE: not reachable ({type(e).__name__}); expected when you have no network"
              " path to the breaker")

    print()
    if problems:
        for p in problems:
            print(f"  - {p}")
        print("infra check: CHANGED without a change request")
        return 1
    print("infra check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
