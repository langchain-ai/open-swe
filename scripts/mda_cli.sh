#!/usr/bin/env bash
# Install the `mda` CLI into mda/'s environment when that project takes
# managed-deepagents from an SDK commit: a git install carries the runtime but
# not the CLI binary its PyPI wheels bundle. Builds the CLI from that commit, in
# MDA_SDK_DIR when set (a checkout of it), else in a clone under mda/.sdk.
# Does nothing for the released package. Needs cargo, and read access to the SDK.
# `mda_cli.sh sha` prints the commit, empty for the released package.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_URL="https://github.com/langchain-ai/managed-deepagents-sdk"

sha="$(uv run --no-project --quiet --python ">=3.11" python - "$ROOT/mda/pyproject.toml" <<'EOF'
import re
import sys
import tomllib

sources = tomllib.load(open(sys.argv[1], "rb")).get("tool", {}).get("uv", {}).get("sources", {})
source = next(
    (v for k, v in sources.items() if re.sub(r"[-_.]+", "-", k).lower() == "managed-deepagents"),
    None,
)
print(source.get("rev", "") if isinstance(source, dict) and "git" in source else "")
EOF
)"
if [[ "${1:-}" == "sha" ]]; then
  echo "$sha"
  exit 0
fi
if [[ -z "$sha" ]]; then
  exit 0
fi

sdk="${MDA_SDK_DIR:-$ROOT/mda/.sdk}"
if [[ ! -e "$sdk/.git" ]]; then
  git clone --quiet --filter=blob:none "$SDK_URL" "$sdk"
fi
if [[ "$(git -C "$sdk" rev-parse HEAD)" != "$sha" ]]; then
  git -C "$sdk" fetch --quiet origin "$sha"
  git -C "$sdk" checkout --quiet --detach "$sha"
fi
cargo build --quiet --release --locked --manifest-path "$sdk/Cargo.toml"

(cd "$ROOT/mda" && uv sync --locked --quiet)
package="$("$ROOT/mda/.venv/bin/python" -c 'import managed_deepagents, pathlib; print(pathlib.Path(managed_deepagents.__file__).parent)')"
mkdir -p "$package/_binaries"
install -m 0755 "$sdk/target/release/mda" "$package/_binaries/mda"
echo "Installed mda from ${SDK_URL}@${sha}"
