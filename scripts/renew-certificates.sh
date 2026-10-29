#!/usr/bin/env bash
# Renew only this deployment's certificates with its own pinned tool image.
set -euo pipefail
umask 077
[[ $(id -u) == 0 ]]
exec 9>/run/lock/agent-council-cert-renew.lock
flock -n 9 || exit 0

certificate_state() {
  find -L /etc/hermes/letsencrypt/live -type f -name fullchain.pem \
    -exec sha256sum {} + | LC_ALL=C sort
}
before=$(certificate_state)
bash /opt/agent-council/scripts/compose-server.sh run --rm --no-deps certbot \
  renew --quiet --config-dir /etc/hermes/letsencrypt \
  --work-dir /var/lib/hermes-acme --logs-dir /var/log/hermes-acme "$@"
after=$(certificate_state)
if [[ "$before" != "$after" ]]; then
  nginx -t
  systemctl reload nginx
fi
