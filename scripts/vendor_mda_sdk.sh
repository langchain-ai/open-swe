#!/usr/bin/env bash
# Replaces vendor/managed-deepagents-sdk with the Managed Deep Agents SDK at a ref
# (default: main): the `mda` CLI's Rust source and the `managed-deepagents` Python
# runtime, which mda/ installs and `make mda-cli` builds the CLI from.
#
#   scripts/vendor_mda_sdk.sh [ref]
#
# MDA_SDK_REPO overrides the upstream, e.g. a local checkout.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/managed-deepagents-sdk"
REPO="${MDA_SDK_REPO:-git@github.com:langchain-ai/managed-deepagents-sdk.git}"
REF="${1:-main}"
PATHS=(Cargo.toml Cargo.lock rustfmt.toml LICENSE README.md crates packages/pypi)

CLONE="$(mktemp -d)"
trap 'rm -rf "$CLONE"' EXIT
git -C "$CLONE" init --quiet
git -C "$CLONE" fetch --quiet --depth 1 "$REPO" "$REF"
COMMIT="$(git -C "$CLONE" rev-parse FETCH_HEAD)"

rm -rf "$DEST"
mkdir -p "$DEST"
git -C "$CLONE" archive FETCH_HEAD "${PATHS[@]}" | tar -x -C "$DEST"
# `make mda-cli` bakes this into `mda --version` instead of this repository's HEAD.
git -C "$CLONE" show -s --format='%H %cs' FETCH_HEAD >"$DEST/UPSTREAM"

echo "Vendored $REPO at $COMMIT into ${DEST#"$ROOT"/}."
echo "Run \`cd mda && uv lock\` if the runtime's dependencies changed."
