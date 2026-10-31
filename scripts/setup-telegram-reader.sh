#!/usr/bin/env bash
# Authorize a user-account collector privately; no credentials in arguments or logs.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ! "$1" =~ ^[A-Za-z][A-Za-z0-9_]{4,31}$ ]]; then
  echo "Usage: bash scripts/setup-telegram-reader.sh SOURCE_USERNAME [--reauthorize]" >&2
  exit 2
fi
if [[ $# -eq 2 && "$2" != --reauthorize ]]; then
  echo "The only optional argument is --reauthorize." >&2
  exit 2
fi
reader_source="$1"
reader_flag="${2:-}"
# Both remote arguments are restricted to validated literals above.
# shellcheck disable=SC2029
exec ssh -tt \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" \
  "python3 /opt/agent-council/scripts/manage.py configure-telegram-monitor $reader_source $reader_flag"
