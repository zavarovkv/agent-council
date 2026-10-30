#!/usr/bin/env bash
# Secrets are entered on the server's private terminal, never in arguments or chat.
set -euo pipefail
exec ssh -t \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes -o ConnectTimeout=15 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" \
  'cd /opt/agent-council && python3 scripts/manage.py configure-backups'
