#!/usr/bin/env bash
# Execute on the server as root. Installs only the separate Hermes instance.
set -euo pipefail
umask 077

revision=bf0203a794a07aac3ff764972ab9087baa4e4f4e
deployment=/opt/hermes-deploy

if [[ $(id -u) != 0 ]]; then
  printf 'Run this script as root on the deployment server.\n' >&2
  exit 1
fi
if [[ -e /opt/hermes || -e "$deployment" ]] || getent passwd hermes >/dev/null; then
  printf 'Hermes paths or user already exist; inspect before proceeding.\n' >&2
  exit 1
fi
systemctl is-active --quiet tl-dashboard-web.service
[[ $(curl -fsS --max-time 15 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
if ss -lntH | awk '{print $4}' | grep -Eq ':9119$'; then
  printf 'Port 9119 is occupied.\n' >&2
  exit 1
fi

install -d -m 0700 "$deployment"
tar -C / -czf "$deployment/nginx-before.tar.gz" etc/nginx
sha256sum /etc/nginx/nginx.conf /etc/nginx/sites-enabled/tl-dashboard > "$deployment/baseline.sha256"
systemctl show tl-dashboard-web.service -p MainPID -p ActiveEnterTimestamp > "$deployment/baseline-service.txt"

useradd --system --create-home --home-dir /var/lib/hermes --shell /usr/sbin/nologin hermes
chmod 0700 /var/lib/hermes
install -d -o hermes -g hermes -m 0750 /opt/hermes
install -d -o hermes -g hermes -m 0700 /var/lib/hermes/.hermes
install -d -m 0755 /opt/hermes/extensions
curl --fail --silent --show-error --location --retry 2 --max-time 90 \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/$revision/scripts/install.sh" \
  -o /opt/hermes/bootstrap.sh
chmod 0644 /opt/hermes/bootstrap.sh
bash -n /opt/hermes/bootstrap.sh
printf '%s\n' "$revision" > "$deployment/revision"

# The installer cannot modify the existing application, host packages or proxy.
systemd-run --unit=hermes-install --collect \
  --property=User=hermes --property=Group=hermes \
  --property=WorkingDirectory=/var/lib/hermes \
  --property=MemoryHigh=1536M --property=MemoryMax=2G \
  --property=CPUQuota=100% --property=Nice=10 \
  --property=ProtectSystem=strict --property=ProtectHome=true \
  --property=PrivateTmp=true --property=NoNewPrivileges=true \
  --property=ReadWritePaths='/opt/hermes /var/lib/hermes' \
  --property=InaccessiblePaths='/opt/tl-dashboard /etc/nginx /etc/letsencrypt' \
  /usr/bin/env HOME=/var/lib/hermes HERMES_HOME=/var/lib/hermes/.hermes \
  HERMES_WEB_BUILD_LIGHT=1 \
  /bin/bash /opt/hermes/bootstrap.sh \
  --commit "$revision" --channel stable --dir /opt/hermes/app \
  --non-interactive --skip-browser --skip-computer-use

printf 'Hermes installer started separately; inspect journalctl -u hermes-install.\n'
