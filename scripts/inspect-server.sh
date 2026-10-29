#!/usr/bin/env bash
# Collect deployment information over SSH without changing server configuration.
# Authentication is handled by SSH; credentials are never stored in this file.
set -eu

ssh -T \
  -i "${COUNCIL_SSH_KEY:-$HOME/.ssh/agent_council_ed25519}" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=15 \
  root@2.29.13.129 bash -s <<'REMOTE'
set -u

section() {
  printf '\n%s\n' "$1"
}

section 'Operating system'
uname -sr
if [ -r /etc/os-release ]; then
  cat /etc/os-release
fi

section 'Resources'
uptime
command -v free >/dev/null 2>&1 && free -m
df -h /

section 'Listening ports'
if command -v ss >/dev/null 2>&1; then
  ss -lntp
fi

section 'Running services'
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --state=running --no-pager
fi

section 'Docker projects and containers'
if command -v docker >/dev/null 2>&1; then
  docker version --format '{{.Server.Version}}'
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  docker compose ls 2>/dev/null || true
fi

section 'Existing project locations'
for directory in /opt /srv /var/www; do
  if [ -d "$directory" ]; then
    ls -ld "$directory"
    find "$directory" -mindepth 1 -maxdepth 2 -type d 2>/dev/null
  fi
done

section 'Reverse proxy availability'
for program in nginx caddy apache2ctl; do
  command -v "$program" 2>/dev/null || true
done

section 'Nginx hostnames and listeners'
if command -v nginx >/dev/null 2>&1; then
  # Only print routing identifiers, never the full configuration or credentials.
  nginx -T 2>/dev/null |
    awk '/^[[:space:]]*(server_name|listen)[[:space:]]/ { print }'
fi

section 'Firewall status'
if command -v ufw >/dev/null 2>&1; then
  ufw status
elif command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --list-services
  firewall-cmd --list-ports
fi

section 'Inspection complete'
REMOTE
