#!/usr/bin/env bash
# Send the reviewed script through stdin; never modify the running image.
set -euo pipefail
repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec bash "$repository/scripts/compose-server.sh" exec -T --user hermes \
  --workdir /opt/hermes hermes /opt/hermes/.venv/bin/python - "$@" \
  < "$repository/scripts/sync-waitlist.py"
