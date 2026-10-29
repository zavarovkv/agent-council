#!/usr/bin/env bash
# Prepare the custom hostname without switching the existing Hermes URL.
set -euo pipefail
umask 077

assets=/opt/hermes-deploy/assets
staging=/opt/hermes-deploy/council
site=/etc/nginx/sites-available/council
enabled=/etc/nginx/sites-enabled/council

[[ $(id -u) == 0 ]]
systemctl is-active --quiet hermes-dashboard.service
systemctl is-active --quiet tl-dashboard-web.service
sha256sum -c /opt/hermes-deploy/baseline.sha256
install -d -m 0700 "$staging"

for kind in http https; do
  template="$assets/config/nginx-hermes.conf"
  [[ "$kind" != http ]] || template="$assets/config/nginx-hermes-http.conf"
  sed -e 's/hermes\.2\.29\.13\.129\.sslip\.io/council.zavarov.com/g' \
    -e 's/hermes_connection_upgrade/council_connection_upgrade/g' \
    "$template" > "$staging/council-$kind.conf"
done
sed 's/hermes\.2\.29\.13\.129\.sslip\.io/council.zavarov.com/g' \
  /opt/hermes/extensions/configure-telegram.py > "$staging/configure-telegram.py"

if [[ ! -e "$staging/hermes-before.conf" ]]; then
  cp -a /etc/nginx/sites-available/hermes "$staging/hermes-before.conf"
  cp -a /var/lib/hermes/.hermes/config.yaml "$staging/hermes-config-before.yaml"
  cp -a /opt/hermes/extensions/configure-telegram.py "$staging/configure-telegram-before.py"
fi
if [[ -e "$site" ]] && ! cmp -s "$site" "$staging/council-http.conf"; then
  printf 'Council virtual host already differs from staging; inspect before proceeding.\n' >&2
  exit 1
fi
if [[ -e "$enabled" && ! -L "$enabled" ]]; then
  printf 'Council enabled path already exists and is not our symlink.\n' >&2
  exit 1
fi
if [[ -L "$enabled" && $(readlink "$enabled") != "$site" ]]; then
  printf 'Council enabled path points elsewhere; refusing to replace it.\n' >&2
  exit 1
fi

rollback_staging() {
  unlink "$enabled" 2>/dev/null || true
  nginx -t && systemctl reload nginx
}
trap rollback_staging ERR
install -m 0644 "$staging/council-http.conf" "$site"
[[ -L "$enabled" ]] || ln -s "$site" "$enabled"
nginx -t
systemctl reload nginx

challenge=/var/www/hermes-acme/.well-known/acme-challenge/council-preflight
printf 'council-acme-ready\n' > "$challenge"
chmod 0644 "$challenge"
[[ $(curl -fsS --retry 3 --retry-all-errors --retry-delay 1 --max-time 10 \
  --resolve council.zavarov.com:80:127.0.0.1 \
  http://council.zavarov.com/.well-known/acme-challenge/council-preflight) == council-acme-ready ]]
unlink "$challenge"
sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
trap - ERR
printf 'council.zavarov.com HTTP challenge route verified; HTTPS candidates prepared.\n'
printf 'Hermes still uses its existing HTTPS URL until DNS and a new certificate are ready.\n'
