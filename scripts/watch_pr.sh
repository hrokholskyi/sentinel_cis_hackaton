#!/usr/bin/env bash
# Wait for the pull request that the investigator agent opened, save its verdict, rules and report
# under .state/pr/<number>/ and apply the verdict to the breaker. Run with --help for options.
set -u
. "$(dirname "$0")/_python.sh"
exec "$PY" "$ROOT/scripts/watch_pr.py" "$@"
