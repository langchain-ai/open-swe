#!/usr/bin/env bash
set -euo pipefail

GH_VERSION=2.100.0

case "$(uname -m)" in
  x86_64)
    arch=amd64
    sha256=e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be
    ;;
  aarch64|arm64)
    arch=arm64
    sha256=ea4e7a581a32ccad6cc7923cb1576ac5859ba4b9a16ab22eb8f8a96e78e2e961
    ;;
  *)
    echo "unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

archive="gh_${GH_VERSION}_linux_${arch}.tar.gz"
root="gh_${GH_VERSION}_linux_${arch}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${archive}" -o "$tmp/$archive"
echo "$sha256  $tmp/$archive" | sha256sum -c -
tar -xzf "$tmp/$archive" -C "$tmp"
install -m 0755 "$tmp/$root/bin/gh" /usr/local/bin/gh

gh --version
gh api --paginate --slurp --help >/dev/null
gh pr checks --json name,bucket,state,workflow,link --help >/dev/null
