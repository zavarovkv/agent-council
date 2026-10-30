#!/usr/bin/env bash
# One-time switch of the existing Docker deployment to council.zavarov.com.
set -euo pipefail
# shellcheck source=scripts/legacy-guard.sh
source "$(dirname "${BASH_SOURCE[0]}")/legacy-guard.sh"
umask 077
[[ $(id -u) == 0 ]]
repository=/opt/agent-council
backup=/opt/hermes-deploy/council/docker-before-domain
profile=/var/lib/agent-council/hermes
[[ -f /etc/hermes/letsencrypt/live/council.zavarov.com/fullchain.pem ]]
[[ ! -e "$backup" ]]
sha256sum -c /opt/hermes-deploy/baseline.sha256
install -d -m 0700 "$backup"
cp -a /etc/agent-council/compose.env "$backup/compose.env"
cp -a /etc/nginx/sites-available/council "$backup/council.conf"
cp -a /etc/nginx/sites-available/hermes "$backup/hermes.conf"
cp -a "$profile/config.yaml" "$backup/config.yaml"

rollback_domain() {
  bash "$repository/scripts/compose-server.sh" stop || true
  cp -a "$backup/compose.env" /etc/agent-council/compose.env
  cp -a "$backup/config.yaml" "$profile/config.yaml"
  cp -a "$backup/council.conf" /etc/nginx/sites-available/council
  cp -a "$backup/hermes.conf" /etc/nginx/sites-available/hermes
  nginx -t && systemctl reload nginx
  bash "$repository/scripts/compose-server.sh" up -d --wait --wait-timeout 150
  printf 'Domain switch failed; the previous Hermes URL was restored.\n' >&2
}
trap rollback_domain ERR

install -m 0644 "$repository/config/nginx-council.conf" /etc/nginx/sites-available/council
nginx -t
systemctl reload nginx
bash "$repository/scripts/compose-server.sh" stop
docker run --rm --network none --memory 512m --cpus 1 --user 995:982 \
  --entrypoint /opt/hermes/.venv/bin/python \
  -v "$profile:/opt/data" \
  -v "$repository/scripts/prepare-docker-profile.py:/tmp/prepare-profile.py:ro" \
  agent-council-hermes:local /tmp/prepare-profile.py /opt/data/config.yaml https://council.zavarov.com
sed -i 's|^HERMES_PUBLIC_URL=.*|HERMES_PUBLIC_URL=https://council.zavarov.com|' /etc/agent-council/compose.env
bash "$repository/scripts/compose-server.sh" up -d --wait --wait-timeout 150

curl -fsS --max-time 15 https://council.zavarov.com/api/status | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d.get("auth_required") is True; assert d.get("auth_providers")==["telegram-owner"]; print("New domain auth gate: OK")'
install -m 0644 "$repository/config/nginx-hermes-redirect.conf" /etc/nginx/sites-available/hermes
nginx -t
systemctl reload nginx
sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
trap - ERR
printf 'https://council.zavarov.com is active; the initial hostname redirects there.\n'
