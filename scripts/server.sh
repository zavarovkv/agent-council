#!/usr/bin/env bash
# Run a command on the deployment host with the dedicated SSH identity.
set -euo pipefail

exec ssh -T \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o ConnectionAttempts=1 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" "$@"
