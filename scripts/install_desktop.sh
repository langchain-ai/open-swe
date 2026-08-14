#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Open SWE Desktop can only be installed with this command on macOS." >&2
  exit 1
fi

for command in node corepack ditto; do
  command -v "$command" >/dev/null || {
    echo "Missing $command. Install Node.js 22, then try again." >&2
    exit 1
  }
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

corepack pnpm install --frozen-lockfile
rm -rf desktop/dist
corepack pnpm --dir desktop run pack

apps=(desktop/dist/mac*/"Open SWE.app")
if [[ ${#apps[@]} -ne 1 || ! -d "${apps[0]}" ]]; then
  echo "Could not find the packaged Open SWE application." >&2
  exit 1
fi

system_app="/Applications/Open SWE.app"
user_app="$HOME/Applications/Open SWE.app"
if [[ -w /Applications ]]; then
  target="$system_app"
else
  mkdir -p "$HOME/Applications"
  target="$user_app"
fi

staged="${target}.installing.$$"
trap 'rm -rf "$staged"' EXIT
ditto "${apps[0]}" "$staged"
osascript -e 'tell application id "com.langchain.openswe" to quit' >/dev/null 2>&1 || true
rm -rf "$target"
mv "$staged" "$target"
trap - EXIT
open "$target"
echo "Open SWE Desktop installed at $target"
