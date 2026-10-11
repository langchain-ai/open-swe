#!/usr/bin/env bash
# Clone or fetch a public repository and check out a pull request's head commit.
# Inputs: REPO_URL, REPO_DIR, HEAD_SHA, and optionally BASE_SHA and PULL_REF.
set -euo pipefail

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  git fetch --all --quiet || true
else
  # Blobs download on demand: a full clone of a large repository outlasts the timeout.
  git clone --quiet --filter=blob:none --no-checkout "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

if [ -n "${BASE_SHA:-}" ]; then
  git fetch origin "$BASE_SHA" --quiet 2>/dev/null || true
fi
git fetch origin "$HEAD_SHA" --quiet 2>/dev/null || true
if [ -n "${PULL_REF:-}" ]; then
  # Fork pull requests are only reachable through their pull ref.
  git fetch origin "$PULL_REF" --quiet 2>/dev/null || true
fi

git checkout --force "$HEAD_SHA" --quiet
[ "$(git rev-parse HEAD)" = "$HEAD_SHA" ]
