#!/usr/bin/env bash
# Run on the server after uploading this repository to /opt/hermes-deploy/assets.
set -euo pipefail
umask 077

assets=/opt/hermes-deploy/assets
hermes_home=/var/lib/hermes/.hermes
cli=/var/lib/hermes/.local/bin/hermes
[[ $(id -u) == 0 ]]
[[ $(runuser -u hermes -- git -C /opt/hermes/app rev-parse HEAD) == bf0203a794a07aac3ff764972ab9087baa4e4f4e ]]
sha256sum -c /opt/hermes-deploy/baseline.sha256

install -d -m 0700 /etc/hermes
if [[ ! -e /etc/hermes/telegram.env ]]; then
  install -m 0600 "$assets/config/telegram.env.example" /etc/hermes/telegram.env
fi
install -d -o hermes -g hermes -m 0700 "$hermes_home/plugins"
install -d -o root -g root -m 0755 "$hermes_home/plugins/telegram-owner"
for name in __init__.py owner.py plugin.yaml; do
  install -o root -g root -m 0644 "$assets/src/telegram_auth/$name" "$hermes_home/plugins/telegram-owner/$name"
done
install -o root -g root -m 0755 "$assets/scripts/configure-telegram.py" /opt/hermes/extensions/configure-telegram.py
install -d -m 0755 /var/www/hermes-status
install -m 0644 "$assets/assets/setup-pending.html" /var/www/hermes-status/setup-pending.html
install -m 0644 /dev/null /var/www/hermes-status/setup-pending
install -d -o hermes -g hermes -m 0700 /var/lib/hermes/workspace
cp -a "$hermes_home/config.yaml" /opt/hermes-deploy/config-before.yaml

hermes_config() {
  runuser -u hermes -- env HOME=/var/lib/hermes HERMES_HOME="$hermes_home" \
    "$cli" config set "$@"
}
hermes_config plugins.disabled '["dashboard_auth/basic", "dashboard_auth/nous", "dashboard_auth/self_hosted", "basic", "nous", "self-hosted"]'
hermes_config plugins.enabled '["telegram-owner"]'
hermes_config dashboard.public_url https://hermes.2.29.13.129.sslip.io
hermes_config dashboard.trusted_proxies '["127.0.0.1", "::1"]'
hermes_config terminal.cwd /var/lib/hermes/workspace

install -o root -g root -m 0644 "$assets/config/hermes-dashboard.service" /etc/systemd/system/hermes-dashboard.service
systemd-analyze verify /etc/systemd/system/hermes-dashboard.service
systemctl daemon-reload
systemctl enable --now hermes-dashboard.service

printf 'Hermes service started on loopback. No public proxy has been enabled yet.\n'
