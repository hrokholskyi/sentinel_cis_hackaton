"""Record a change request: regenerate rules/rules.lock and append an entry to CHANGELOG.md.

The entry lists the short hashes of all commits that touched monitored paths and are not
listed yet. Refuses to run with uncommitted changes in monitored paths. Commit the two
files afterwards, in a commit of their own.
"""

import sys
from datetime import date

import changelog as cl


def main(argv: list[str]) -> int:
    description = " ".join(argv).strip()
    if not description or "\n" in description:
        print('usage: scripts/change_request.sh "<description>"', file=sys.stderr)
        return 2
    try:
        dirty = cl.uncommitted()
        if dirty:
            print("refused: uncommitted changes in monitored paths; commit them first:",
                  file=sys.stderr)
            for line in dirty:
                print("  " + line, file=sys.stderr)
            return 1
        text = cl.changelog_text()
        new = [c for c in cl.commits() if not cl.covered(c["hash"], text)]
    except cl.GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cl.LOCK.write_text(cl.lock_text())
    if not text:
        text = cl.CHANGELOG_HEADER + "\n\n# Change log\n"
    entry = [f"## {date.today().isoformat()} - {description}", ""]
    entry.append("- Commits: " + (", ".join(c["short"] for c in new) if new else "none") + ".")
    entry.extend(f"  - {c['short']} {c['subject']}" for c in new)
    entry.append(f"- rules/rules.lock: {len(cl.rule_checksums())} rule files.")
    cl.CHANGELOG.write_text(text.rstrip("\n") + "\n\n" + "\n".join(entry) + "\n")
    print(f"rules/rules.lock regenerated; CHANGELOG.md entry added with {len(new)} commits")
    print("next: commit CHANGELOG.md and rules/rules.lock in a commit of their own")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
