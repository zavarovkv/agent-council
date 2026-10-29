#!/usr/bin/env bash
# Enter the OIDC secret in the terminal, never in chat or shell history.
set -euo pipefail
exec ssh -t \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes -o ConnectTimeout=15 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" \
  'python3 /opt/hermes/extensions/configure-telegram.py'
