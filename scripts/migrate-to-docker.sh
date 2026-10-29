#!/usr/bin/env bash
# Cut over only after the independently running Docker candidate is healthy.
set -euo pipefail
umask 077
[[ $(id -u) == 0 ]]
repository=/opt/agent-council
profile=/var/lib/agent-council/hermes
workspace=/var/lib/agent-council/workspace
backup=/opt/hermes-deploy/docker-migration
image=agent-council-hermes:local
[[ ! -e "$profile" && ! -e "$workspace" ]]
[[ -d /var/lib/agent-council/hermes-staging ]]
sha256sum -c /opt/hermes-deploy/baseline.sha256
cp -a /etc/agent-council/compose.env "$backup/staging-compose.env"

rollback_native() {
  bash "$repository/scripts/compose-server.sh" stop || true
  systemctl enable --now hermes-dashboard.service
  printf 'Docker cutover failed; native Hermes restored. Data and backups retained.\n' >&2
}
trap rollback_native ERR
bash "$repository/scripts/compose-server.sh" stop
systemctl stop hermes-dashboard.service
# The stopped profile yields consistent SQLite and authentication state.
tar -C /var/lib/hermes -cf "$backup/hermes-native-before-docker.tar" .hermes workspace
install -d -o hermes -g hermes -m 0700 "$profile" "$workspace"
cp -a /var/lib/hermes/.hermes/. "$profile/"
cp -a /var/lib/hermes/workspace/. "$workspace/"
install -d -o hermes -g hermes -m 0700 "$profile/migration-backup"
if [[ -d "$profile/plugins/telegram-owner" ]]; then
  mv "$profile/plugins/telegram-owner" "$profile/migration-backup/telegram-owner"
fi
install -m 0600 "$repository/config/docker.env.example" /etc/agent-council/compose.env
docker run --rm --network none --memory 512m --cpus 1 --user 995:982 \
  --entrypoint /opt/hermes/.venv/bin/python \
  -v "$profile:/opt/data" \
  -v "$repository/scripts/prepare-docker-profile.py:/tmp/prepare-profile.py:ro" \
  "$image" /tmp/prepare-profile.py /opt/data/config.yaml https://hermes.2.29.13.129.sslip.io
bash "$repository/scripts/compose-server.sh" up -d --wait --wait-timeout 150

# Prove that mutable data survives a real container restart.
bash "$repository/scripts/compose-server.sh" exec -T --user 995:982 hermes \
  python3 -c 'from pathlib import Path; Path("/opt/data/.council-persistence-check").write_text("persisted", encoding="utf-8")'
bash "$repository/scripts/compose-server.sh" restart hermes
bash "$repository/scripts/compose-server.sh" up -d --wait --wait-timeout 150
bash "$repository/scripts/compose-server.sh" exec -T --user 995:982 hermes \
  python3 -c 'from pathlib import Path; p=Path("/opt/data/.council-persistence-check"); assert p.read_text(encoding="utf-8")=="persisted"; p.unlink(); print("Persistence across restart: OK")'

sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
systemctl disable hermes-dashboard.service
printf 'docker\n' > /etc/hermes/deployment-kind
install -m 0755 "$repository/scripts/configure-telegram.py" /opt/hermes/extensions/configure-telegram.py
trap - ERR
printf 'Docker Hermes now serves localhost:9119. Native profile and backup retained.\n'
