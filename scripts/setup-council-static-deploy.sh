#!/usr/bin/env bash
# Operator-only bootstrap/rotation. Input is one public Ed25519 key, never a secret.
set -euo pipefail

[[ $EUID == 0 && $# == 1 ]] || { echo "Usage (root): $0 PUBLIC_KEY_FILE" >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
deploy_home=/var/lib/council-deploy
libexec=/usr/local/libexec/council
sudo_rule=/etc/sudoers.d/council-landing-deploy
public_key=$(cat -- "$1")
[[ "$public_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [^\$\'\"\`]+)?$ && "$public_key" != *$'\n'* ]] || {
  echo "Expected exactly one plain Ed25519 public key" >&2; exit 1;
}
ssh-keygen -lf "$1" >/dev/null

# Do not race another deployment/backup or follow operator path symlinks.
for path in /etc/agent-council /etc/agent-council/operations.lock /usr/local/libexec "$libexec" "$deploy_home" "$deploy_home/.ssh" "$deploy_home/.ssh/authorized_keys" "$sudo_rule"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
  if [[ -e "$path" ]]; then
    [[ $(stat -c %u "$path") == 0 ]] || { echo "Expected root ownership: $path" >&2; exit 1; }
    [[ -z $(find "$path" -maxdepth 0 -perm /022 -print) ]] || { echo "Unsafe permissions: $path" >&2; exit 1; }
  fi
done
[[ -d /etc/agent-council && -f /etc/agent-council/operations.lock ]]
exec 9<>/etc/agent-council/operations.lock
flock -w 120 9

if getent passwd council-deploy >/dev/null; then
  [[ $(getent passwd council-deploy | cut -d: -f6-7) == "$deploy_home:/bin/sh" ]]
  [[ $(id -Gn council-deploy) == council-deploy ]]
  [[ $(passwd -S council-deploy | cut -d' ' -f2) == L ]]
else
  useradd --system --user-group --home-dir "$deploy_home" --no-create-home --shell /bin/sh council-deploy
fi
install -d -o root -g root -m 0755 /usr/local/libexec "$libexec" "$deploy_home" "$deploy_home/.ssh"
for name in council-landing-deploy.py install-council-landing.py verify-council-landing.py; do
  [[ ! -L "$libexec/$name" ]]
  install -o root -g root -m 0755 "$source_dir/$name" "$libexec/$name"
done

staging=$(mktemp -d /etc/council-static-deploy.XXXXXX)
trap 'rm -rf -- "$staging"' EXIT
cat > "$staging/sudoers" <<'RULE'
Defaults:council-deploy env_reset, secure_path=/usr/sbin:/usr/bin:/sbin:/bin
council-deploy ALL=(root) NOPASSWD: /usr/local/libexec/council/install-council-landing.py
RULE
chmod 0440 "$staging/sudoers"
visudo -cf "$staging/sudoers"
install -o root -g root -m 0440 "$staging/sudoers" "$sudo_rule"
visudo -c >/dev/null
printf 'restrict,command="/usr/local/libexec/council/council-landing-deploy.py" %s\n' "$public_key" > "$staging/authorized_keys"
install -o root -g root -m 0644 "$staging/authorized_keys" "$deploy_home/.ssh/authorized_keys"
sshd -t
echo "Restricted Council static receiver installed; no service restart required."
