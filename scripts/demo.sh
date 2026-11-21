#!/usr/bin/env bash
# The one command for the stage: fresh seed, bank and breaker, dashboard, guided demo.
# Usage: scripts/demo.sh [--no-open] [--no-webhook] [demo.py options, e.g. --auto, --from 3, --no-tour]
#
# Loads .env if present. Picks JEV_MODE=live when TYPESAFE_API_KEY is set and JEV_MODE is
# not, else stub. Reseeds bank.db and breaker.db (BANK_DB, BREAKER_DB), starts the bank and
# the breaker on the ports of BANK_URL and BREAKER_URL (default 8001 and 8000), refuses if
# a port is busy, opens the dashboard, runs scripts/demo.py, and on exit stops exactly the
# two services it started. It never prints secrets. ADMIN_TOKEN and SIM_TOKEN get random
# values per run unless set; they are written to .state/demo/tokens.env (mode 600).
set -u
. "$(dirname "$0")/_python.sh"

if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi
if [ -z "${JEV_MODE:-}" ]; then
    if [ -n "${TYPESAFE_API_KEY:-}" ]; then JEV_MODE=live; else JEV_MODE=stub; fi
fi
export JEV_MODE
export BANK_URL="${BANK_URL:-http://127.0.0.1:8001}"
export BREAKER_URL="${BREAKER_URL:-http://127.0.0.1:8000}"
export PYTHON="$PY"

OPEN=1
ARGS=()
for a in "$@"; do
    case "$a" in
        --no-open) OPEN=0 ;;
        # Rehearse without starting a Devin session, even when .env has the webhook
        --no-webhook) unset DEVIN_WEBHOOK_URL DEVIN_WEBHOOK_SECRET ;;
        *) ARGS+=("$a") ;;
    esac
done

port_of() {
    "$PY" -c 'import sys, urllib.parse as u; print(u.urlsplit(sys.argv[1]).port or 80)' "$1"
}
port_busy() {
    "$PY" - "$1" <<'EOF'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
EOF
}
BANK_PORT="$(port_of "$BANK_URL")" || exit 2
BREAKER_PORT="$(port_of "$BREAKER_URL")" || exit 2
for p in "$BANK_PORT" "$BREAKER_PORT"; do
    if port_busy "$p"; then
        echo "error: port $p is busy. Stop whatever runs there (lsof -nP -iTCP:$p -sTCP:LISTEN) and try again." >&2
        exit 2
    fi
done

LOGS="$ROOT/.state/demo"
mkdir -p "$LOGS" || exit 2

# Per-run tokens: the development defaults are public. Generate random ones unless the
# environment or .env set their own, export them to everything started below, and write
# them to a file only the owner can read. The values are never printed.
DEV_SIM_TOKEN="$("$PY" -c 'import sys; sys.path.insert(0, "scripts"); import common; print(common.DEV_SIM_TOKEN)')" || exit 2
new_token() { "$PY" -c 'import secrets; print(secrets.token_urlsafe(24))'; }
if [ -z "${ADMIN_TOKEN:-}" ] || [ "$ADMIN_TOKEN" = "dev-admin-token" ]; then
    ADMIN_TOKEN="$(new_token)" || exit 2
fi
if [ -z "${SIM_TOKEN:-}" ] || [ "$SIM_TOKEN" = "$DEV_SIM_TOKEN" ]; then
    SIM_TOKEN="$(new_token)" || exit 2
fi
export ADMIN_TOKEN SIM_TOKEN
(umask 077; printf 'ADMIN_TOKEN=%q\nSIM_TOKEN=%q\n' "$ADMIN_TOKEN" "$SIM_TOKEN" >"$LOGS/tokens.env") || exit 2
chmod 600 "$LOGS/tokens.env" || exit 2
echo "tokens for this run: .state/demo/tokens.env (for apply_verdict.sh or watch_pr.sh in another terminal: set -a; . .state/demo/tokens.env; set +a)"
BANK_PID=""
BREAKER_PID=""
stop_services() {
    for pid in "$BREAKER_PID" "$BANK_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
        fi
    done
}
trap stop_services EXIT
trap 'exit 130' INT TERM

echo "jev mode: $JEV_MODE; webhook: $([ -n "${DEVIN_WEBHOOK_URL:-}" ] && echo configured || echo 'not configured (offline mode)')"
echo "seeding fresh databases"
"$PY" "$ROOT/scripts/seed.py" || exit 1

"$PY" -m uvicorn bank.app:app --host 127.0.0.1 --port "$BANK_PORT" >"$LOGS/bank.log" 2>&1 &
BANK_PID=$!
"$PY" -m uvicorn breaker.app:app --host 127.0.0.1 --port "$BREAKER_PORT" >"$LOGS/breaker.log" 2>&1 &
BREAKER_PID=$!
echo "bank pid $BANK_PID on $BANK_URL, breaker pid $BREAKER_PID on $BREAKER_URL (logs in .state/demo/)"

wait_healthy() {
    local url="$1" pid="$2" i=0
    while [ $i -lt 100 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "error: $url exited early, see .state/demo/*.log" >&2
            return 1
        fi
        if curl -fsS -o /dev/null "$url/health" 2>/dev/null; then return 0; fi
        sleep 0.2
        i=$((i + 1))
    done
    echo "error: $url did not become healthy" >&2
    return 1
}
wait_healthy "$BANK_URL" "$BANK_PID" || exit 1
wait_healthy "$BREAKER_URL" "$BREAKER_PID" || exit 1

if [ "$OPEN" = 1 ]; then
    if command -v open >/dev/null 2>&1; then
        open "$BREAKER_URL/"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$BREAKER_URL/" >/dev/null 2>&1
    else
        echo "open the dashboard: $BREAKER_URL/"
    fi
fi

"$PY" "$ROOT/scripts/demo.py" --url "$BREAKER_URL" ${ARGS[@]+"${ARGS[@]}"}
exit $?
