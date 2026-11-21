#!/usr/bin/env bash
# Read-only facts for an investigator: key-history, beneficiary <id>, bases <beneficiary_id>,
# held <incident_id>. Run without arguments for usage.
# Builds a snapshot in .state/ from the committed traces when no databases exist.
set -u
. "$(dirname "$0")/_python.sh"
exec "$PY" "$ROOT/scripts/facts.py" "$@"
