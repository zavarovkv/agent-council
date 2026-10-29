#!/usr/bin/env bash
# Manage invited Telegram IDs remotely; arguments never contain credentials.
set -euo pipefail
if [[ $# -eq 0 ]]; then
  printf 'Usage: bash scripts/telegram-access.sh list | add ID [ID...] | remove ID [ID...]\n' >&2
  exit 2
fi
action=$1
shift
case "$action" in
  list) [[ $# -eq 0 ]] ;;
  add|remove)
    [[ $# -gt 0 ]]
    for account_id in "$@"; do
      [[ "$account_id" =~ ^[0-9]{1,19}$ ]] || { printf 'Expected a numeric Telegram ID.\n' >&2; exit 2; }
    done
    ;;
  *) printf 'Expected list, add or remove.\n' >&2; exit 2 ;;
esac
repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec bash "$repository/scripts/server.sh" \
  python3 /opt/agent-council/scripts/telegram_access.py "$action" "$@"
