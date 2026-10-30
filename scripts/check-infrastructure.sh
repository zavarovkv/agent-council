#!/usr/bin/env bash
# Linux CI/static review tools, downloaded from their official release assets.
set -euo pipefail
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]]
repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${COUNCIL_REVIEW_TOOLS_DIR:-$(mktemp -d)}
mkdir -p "$tools_dir"
curl -fsSL --retry 2 --max-time 90 \
  https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz \
  -o "$tools_dir/shellcheck.tar.xz"
printf '8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198  %s/shellcheck.tar.xz\n' "$tools_dir" | sha256sum -c -
tar -xf "$tools_dir/shellcheck.tar.xz" -C "$tools_dir"
curl -fsSL --retry 2 --max-time 90 \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz \
  -o "$tools_dir/actionlint.tar.gz"
printf '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  %s/actionlint.tar.gz\n' "$tools_dir" | sha256sum -c -
tar -xf "$tools_dir/actionlint.tar.gz" -C "$tools_dir"
"$tools_dir/shellcheck-v0.11.0/shellcheck" "$repository"/scripts/*.sh
"$tools_dir/actionlint" -shellcheck="$tools_dir/shellcheck-v0.11.0/shellcheck" "$repository"/.github/workflows/*.yaml
