#!/usr/bin/env bash
# Select ChatGPT or Codex Subscription, then authorize in your own browser.
set -euo pipefail
exec ssh -t \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes -o ConnectTimeout=15 \
  "${COUNCIL_SSH_HOST:-root@2.29.13.129}" \
  'if [ -f /etc/hermes/deployment-kind ] && [ "$(cat /etc/hermes/deployment-kind)" = docker ]; then exec bash /opt/agent-council/scripts/compose-server.sh exec --user hermes hermes hermes model; else exec runuser -u hermes -- env HOME=/var/lib/hermes HERMES_HOME=/var/lib/hermes/.hermes /var/lib/hermes/.local/bin/hermes model; fi'
