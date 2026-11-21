# Sourced by the scripts in this folder. Works with bash 3.2 (macOS) and GNU bash (Linux).
# Sets ROOT (repo root, also the working directory) and PY (the Python interpreter):
# $PYTHON if set, else .venv/bin/python if it exists, else python3.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="python3"
fi

if ! "$PY" -c 'import fastapi, uvicorn, httpx' >/dev/null 2>&1; then
    echo "error: FastAPI is not importable with $PY; run: pip install -r requirements.txt" >&2
    exit 2
fi
