#!/usr/bin/env bash
# Usage: scripts/apply_verdict.sh <incident_id> <path/to/verdict.json>
# POSTs the verdict to the breaker: $BREAKER_URL/incidents/<incident_id>/verdict with the
# X-Admin-Token header ($ADMIN_TOKEN). Prints the response; exits 1 on a non-2xx status.
# VERDICT_SOURCE=rehearsal sends the header X-Verdict-Source: rehearsal, for a verdict that
# comes from an earlier rehearsal run (the dashboard then shows no pause-to-PR minutes).
set -u

BREAKER_URL="${BREAKER_URL:-http://127.0.0.1:8000}"
ADMIN_TOKEN="${ADMIN_TOKEN:-dev-admin-token}"

if [ $# -ne 2 ]; then
    echo "usage: scripts/apply_verdict.sh <incident_id> <path/to/verdict.json>" >&2
    exit 2
fi
incident_id="$1"
verdict_file="$2"
case "$incident_id" in
    ''|*[!A-Za-z0-9_]*) echo "invalid incident id: $incident_id" >&2; exit 2 ;;
esac
if [ ! -f "$verdict_file" ]; then
    echo "no such file: $verdict_file" >&2
    exit 2
fi

source_header=()
case "${VERDICT_SOURCE:-}" in
    '') ;;
    live|rehearsal) source_header=(-H "X-Verdict-Source: $VERDICT_SOURCE") ;;
    *) echo "VERDICT_SOURCE must be live or rehearsal" >&2; exit 2 ;;
esac

out="$(mktemp "${TMPDIR:-/tmp}/verdict.XXXXXX")" || exit 2
trap 'rm -f "$out"' EXIT

code="$(curl -sS -o "$out" -w '%{http_code}' -X POST \
    -H "Content-Type: application/json" \
    -H "X-Admin-Token: $ADMIN_TOKEN" \
    ${source_header[@]+"${source_header[@]}"} \
    --data-binary "@$verdict_file" \
    "${BREAKER_URL%/}/incidents/$incident_id/verdict")"
rc=$?
cat "$out"
echo
if [ $rc -ne 0 ]; then
    echo "error: could not reach the breaker at $BREAKER_URL (curl exit $rc)" >&2
    exit 1
fi
case "$code" in
    2??) exit 0 ;;
    *) echo "error: HTTP $code" >&2; exit 1 ;;
esac
