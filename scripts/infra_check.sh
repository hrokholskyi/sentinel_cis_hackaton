#!/usr/bin/env bash
# Was our own infrastructure changed? Recent commits in rules/, breaker/ and config, uncommitted
# changes, rule checksums against rules/rules.lock, new API keys, breaker health.
# Exit 1 if anything changed without an entry in CHANGELOG.md, else "infra check: clean".
set -u
. "$(dirname "$0")/_python.sh"
exec "$PY" "$ROOT/scripts/infra_check.py" "$@"
