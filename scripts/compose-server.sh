#!/usr/bin/env bash
# Run on the server; environment and secrets are stored outside the checkout.
set -euo pipefail
exec docker compose \
  --env-file /etc/agent-council/compose.env \
  --project-directory /opt/agent-council \
  -f /opt/agent-council/compose.yaml "$@"
