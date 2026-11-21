#!/usr/bin/env bash
# Replay fixtures/legit.jsonl and then an incident trace against a fresh bank and breaker.
# Usage: scripts/replay.sh [incident_id]   (default: the latest file in fixtures/incidents/)
# Prints the result table. Exit 0 only if attack executed = 0 and legit executed = 100%.
# Offline, no secrets: fresh databases in a temp dir, JEV_MODE=stub, no webhook.
set -u
. "$(dirname "$0")/_python.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/replay.XXXXXX")" || exit 2

cleanup() {
    if [ -f "$TMP/pids" ]; then
        while read -r pid; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
            fi
        done < "$TMP/pids"
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

"$PY" "$ROOT/scripts/replaylib.py" --workdir "$TMP" --pidfile "$TMP/pids" "$@"
rc=$?
exit $rc
