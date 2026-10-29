#!/usr/bin/env bash
# Add only the separate Hermes vhost and issue its own certificate.
set -euo pipefail
umask 077

assets=/opt/hermes-deploy/assets
site=/etc/nginx/sites-available/hermes
enabled=/etc/nginx/sites-enabled/hermes
domain=hermes.2.29.13.129.sslip.io

[[ $(id -u) == 0 ]]
[[ ! -e "$enabled" ]]
if [[ -e "$site" ]] && ! cmp -s "$site" "$assets/config/nginx-hermes-http.conf"; then
  printf 'Refusing to overwrite an existing non-staging Hermes vhost.\n' >&2
  exit 1
fi
systemctl is-active --quiet hermes-dashboard.service
systemctl is-active --quiet tl-dashboard-web.service
sha256sum -c /opt/hermes-deploy/baseline.sha256

rollback_new_vhost() {
  # These are only our newly-created paths; original configurations stay intact.
  unlink "$enabled" 2>/dev/null || true
  nginx -t && systemctl reload nginx
}
trap rollback_new_vhost ERR

install -d -m 0755 /var/www/hermes-acme \
  /var/www/hermes-acme/.well-known /var/www/hermes-acme/.well-known/acme-challenge
install -m 0644 "$assets/config/nginx-hermes-http.conf" "$site"
ln -s "$site" "$enabled"
nginx -t
systemctl reload nginx
[[ $(curl -fsS --max-time 15 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]

# Validate the exact challenge route before making any ACME request.
challenge=/var/www/hermes-acme/.well-known/acme-challenge/council-preflight
printf 'hermes-acme-ready\n' > "$challenge"
chmod 0644 "$challenge"
[[ $(curl -fsS --retry 3 --retry-all-errors --retry-delay 1 --max-time 10 \
  --resolve "$domain:80:127.0.0.1" "http://$domain/.well-known/acme-challenge/council-preflight") == hermes-acme-ready ]]
unlink "$challenge"

# Certbot applies its own restrictive modes to account and private-key files.
umask 022
/opt/tl-dashboard/certbot/bin/certbot certonly --non-interactive --agree-tos \
  --register-unsafely-without-email --webroot --webroot-path /var/www/hermes-acme \
  --config-dir /etc/hermes/letsencrypt --work-dir /var/lib/hermes-acme \
  --logs-dir /var/log/hermes-acme --cert-name "$domain" -d "$domain"
umask 077

install -m 0644 "$assets/config/nginx-hermes.conf" "$site"
nginx -t
systemctl reload nginx
sha256sum -c /opt/hermes-deploy/baseline.sha256
systemctl is-active --quiet tl-dashboard-web.service
[[ $(curl -fsS --max-time 15 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]

for file in hermes-cert-renew.service hermes-cert-renew.timer; do
  install -m 0644 "$assets/config/$file" "/etc/systemd/system/$file"
done
systemd-analyze verify /etc/systemd/system/hermes-cert-renew.service /etc/systemd/system/hermes-cert-renew.timer
systemctl daemon-reload
systemctl enable --now hermes-cert-renew.timer
trap - ERR
printf 'Separate HTTPS address ready: https://%s\n' "$domain"
