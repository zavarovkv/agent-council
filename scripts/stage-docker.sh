#!/usr/bin/env bash
# Start a copied profile on loopback:19119, leaving native Hermes running.
set -euo pipefail
umask 077
[[ $(id -u) == 0 ]]
repository=/opt/agent-council
profile=/var/lib/agent-council/hermes-staging
workspace=/var/lib/agent-council/workspace-staging
image=agent-council-hermes:local
[[ ! -e "$profile" ]]
sha256sum -c /opt/hermes-deploy/baseline.sha256
install -d -m 0700 /etc/agent-council
install -d -o hermes -g hermes -m 0700 "$profile" "$workspace"
cp -a /var/lib/hermes/.hermes/. "$profile/"
cp -a /var/lib/hermes/workspace/. "$workspace/"
install -d -o hermes -g hermes -m 0700 "$profile/migration-backup"
if [[ -d "$profile/plugins/telegram-owner" ]]; then
  mv "$profile/plugins/telegram-owner" "$profile/migration-backup/telegram-owner"
fi
sed -e 's|HERMES_DATA_DIR=.*|HERMES_DATA_DIR=/var/lib/agent-council/hermes-staging|' \
  -e 's|HERMES_WORKSPACE_DIR=.*|HERMES_WORKSPACE_DIR=/var/lib/agent-council/workspace-staging|' \
  -e 's|HERMES_HTTP_PORT=.*|HERMES_HTTP_PORT=19119|' \
  "$repository/config/docker.env.example" > /etc/agent-council/compose.env
docker run --rm --network none --memory 512m --cpus 1 --user 995:982 \
  --entrypoint /opt/hermes/.venv/bin/python \
  -v "$profile:/opt/data" \
  -v "$repository/scripts/prepare-docker-profile.py:/tmp/prepare-profile.py:ro" \
  "$image" /tmp/prepare-profile.py /opt/data/config.yaml \
  "$(sed -n 's/^HERMES_PUBLIC_URL=//p' /etc/agent-council/compose.env)"
bash "$repository/scripts/compose-server.sh" config --quiet
bash "$repository/scripts/compose-server.sh" up -d --wait --wait-timeout 150
sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
printf 'Docker Hermes is healthy on localhost:19119; native Hermes remains active.\n'
