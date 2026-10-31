#!/usr/bin/env bash
# Enter application credentials only through the remote operator's private TTY.
set -euo pipefail
if [[ $# != 0 ]]; then printf 'Usage: setup-linear.sh\n' >&2; exit 2; fi
exec ssh -t \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes -o ConnectTimeout=15 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" \
  'exec python3 /opt/agent-council/scripts/manage.py configure-linear-agents'
