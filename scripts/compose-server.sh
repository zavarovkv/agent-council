#!/usr/bin/env bash
# Run on the server; environment and secrets are stored outside the checkout.
set -euo pipefail
repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
env_file=${COUNCIL_COMPOSE_ENV_FILE:-/etc/agent-council/compose.env}
active_compose="${env_file%/*}/active-compose.yaml"
compose_file="$repository/compose.yaml"
if [[ -f "$active_compose" ]]; then compose_file="$active_compose"; fi
if [[ ${1:-} == config && "$*" != 'config --quiet' ]]; then
  printf 'Use config --quiet; resolved production settings may contain credentials.\n' >&2
  exit 2
fi
if [[ ${1:-} == build || " $* " == *' --build '* ]]; then
  printf 'Use python3 scripts/manage.py install or deploy to build a reviewed release.\n' >&2
  exit 2
fi
while IFS= read -r name; do
  case "$name" in HERMES_*|COUNCIL_*|COMPOSE_*) unset "$name" ;; esac
done < <(compgen -v)
exec docker compose \
  --project-name agent-council \
  --env-file "$env_file" \
  --project-directory "$repository" \
  -f "$compose_file" "$@"
