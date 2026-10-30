#!/usr/bin/env bash
# Sourced by historical, server-specific migration scripts.
if [[ ${COUNCIL_RUN_LEGACY_MIGRATION:-} != true ]]; then
  printf 'Historical migration only. Use scripts/manage.py for current deployments.\n' >&2
  printf 'A reviewed legacy migration requires COUNCIL_RUN_LEGACY_MIGRATION=true.\n' >&2
  exit 2
fi
