#!/usr/bin/env bash
# Install Docker from its official repository; do not upgrade unrelated packages.
set -euo pipefail
umask 077

[[ $(id -u) == 0 ]]
if command -v docker >/dev/null 2>&1; then
  docker version
  docker compose version
  exit 0
fi
systemctl is-active --quiet tl-dashboard-web.service
sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]

backup=/opt/hermes-deploy/docker-migration
install -d -m 0700 "$backup"
ip -4 route > "$backup/routes-before.txt"
if command -v iptables-save >/dev/null 2>&1; then
  iptables-save > "$backup/iptables-before.rules"
fi
systemctl show tl-dashboard-web.service -p MainPID -p ActiveEnterTimestamp > "$backup/service-before.txt"

install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 2 --max-time 60 https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/agent-council-docker.asc
chmod 0644 /etc/apt/keyrings/agent-council-docker.asc
# OS-provided values, not user-controlled shell input.
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == ubuntu ]]
docker_codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
docker_architecture=$(dpkg --print-architecture)
sources=/etc/apt/sources.list.d/agent-council-docker.sources
cat > "$sources" <<REPOSITORY
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $docker_codename
Components: stable
Architectures: $docker_architecture
Signed-By: /etc/apt/keyrings/agent-council-docker.asc
REPOSITORY
chmod 0644 "$sources"
apt-get update -o "Dir::Etc::sourcelist=$sources" \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
docker_version=$(apt-cache madison docker-ce | awk 'NR == 1 {print $3}')
[[ -n "$docker_version" ]]
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive \
  apt-get install -y --no-install-recommends --no-upgrade \
  "docker-ce=$docker_version" "docker-ce-cli=$docker_version" \
  containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version --format '{{.Server.Version}}'
docker compose version
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  > "$backup/docker-package-versions.txt"
sha256sum -c /opt/hermes-deploy/baseline.sha256
[[ $(curl -fsS --max-time 10 -o /dev/null -w '%{http_code}' https://2.29.13.129/) == 200 ]]
printf 'Docker installed. Existing application remains healthy.\n'
