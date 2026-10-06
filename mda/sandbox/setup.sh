#!/usr/bin/env bash
set -euo pipefail

command -v gh >/dev/null 2>&1 && exit 0
SUDO=$([ "$(id -u)" -eq 0 ] || echo sudo)
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | $SUDO tee /usr/share/keyrings/githubcli-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | $SUDO tee /etc/apt/sources.list.d/github-cli.list >/dev/null
$SUDO apt-get update
$SUDO apt-get install -y --no-install-recommends gh git
