#!/usr/bin/env bash
# Usage: scripts/change_request.sh "<description>"
# The only writer of CHANGELOG.md and rules/rules.lock: regenerates rules/rules.lock from the
# rule files and appends a CHANGELOG.md entry with the commits not listed yet.
# Refuses to run with uncommitted changes in monitored paths.
set -u
. "$(dirname "$0")/_python.sh"
exec "$PY" "$ROOT/scripts/change_request.py" "$@"
