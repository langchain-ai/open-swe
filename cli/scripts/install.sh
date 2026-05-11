#!/usr/bin/env sh
# Open SWE CLI installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/langchain-ai/open-swe/main/cli/scripts/install.sh | sh
#
# Environment variables:
#   OPENSWE_REPO         GitHub repo (default: langchain-ai/open-swe)
#   OPENSWE_VERSION      Release tag or "latest" (default: latest)
#   OPENSWE_INSTALL_DIR  Install directory (default: $HOME/.openswe/bin)

set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail

OPENSWE_REPO="${OPENSWE_REPO:-langchain-ai/open-swe}"
OPENSWE_VERSION="${OPENSWE_VERSION:-latest}"
OPENSWE_INSTALL_DIR="${OPENSWE_INSTALL_DIR:-$HOME/.openswe/bin}"

if [ -t 1 ]; then
  C_RESET="$(printf '\033[0m')"
  C_BOLD="$(printf '\033[1m')"
  C_RED="$(printf '\033[31m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_BLUE="$(printf '\033[34m')"
else
  C_RESET=""
  C_BOLD=""
  C_RED=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
fi

info() { printf "%s%s%s\n" "$C_BLUE" "$1" "$C_RESET"; }
warn() { printf "%s%s%s\n" "$C_YELLOW" "$1" "$C_RESET" >&2; }
err()  { printf "%s%s%s\n" "$C_RED" "$1" "$C_RESET" >&2; }
ok()   { printf "%s%s%s\n" "$C_GREEN" "$1" "$C_RESET"; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "required command not found: $1"
    exit 1
  fi
}

need curl
need uname
need chmod
need mkdir
need mv

detect_os() {
  uname_s="$(uname -s)"
  case "$uname_s" in
    Darwin) echo "darwin" ;;
    Linux)  echo "linux" ;;
    *)
      err "unsupported OS: $uname_s"
      exit 1
      ;;
  esac
}

detect_arch() {
  uname_m="$(uname -m)"
  case "$uname_m" in
    arm64|aarch64) echo "arm64" ;;
    x86_64|amd64)  echo "x64" ;;
    *)
      err "unsupported architecture: $uname_m"
      exit 1
      ;;
  esac
}

# Pick sha256 tool that exists on both macOS and Linux.
sha256_of() {
  file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    err "neither sha256sum nor shasum is available"
    exit 1
  fi
}

OS="$(detect_os)"
ARCH="$(detect_arch)"
TARGET="${OS}-${ARCH}"
ASSET="openswe-${TARGET}"

if [ "$OPENSWE_VERSION" = "latest" ]; then
  BASE_URL="https://github.com/${OPENSWE_REPO}/releases/latest/download"
  VERSION_LABEL="latest"
else
  BASE_URL="https://github.com/${OPENSWE_REPO}/releases/download/${OPENSWE_VERSION}"
  VERSION_LABEL="$OPENSWE_VERSION"
fi

info "${C_BOLD}Installing Open SWE CLI${C_RESET}"
info "  repo:    $OPENSWE_REPO"
info "  version: $VERSION_LABEL"
info "  target:  $TARGET"
info "  dest:    $OPENSWE_INSTALL_DIR/openswe"

TMPDIR_="$(mktemp -d 2>/dev/null || mktemp -d -t openswe)"
cleanup() { rm -rf "$TMPDIR_"; }
trap cleanup EXIT INT TERM

ASSET_URL="${BASE_URL}/${ASSET}"
SUMS_URL="${BASE_URL}/SHA256SUMS"
ASSET_PATH="${TMPDIR_}/${ASSET}"
SUMS_PATH="${TMPDIR_}/SHA256SUMS"

info "Downloading $ASSET_URL"
if ! curl -fL --proto '=https' --tlsv1.2 -o "$ASSET_PATH" "$ASSET_URL"; then
  err "failed to download binary from $ASSET_URL"
  exit 1
fi

info "Downloading SHA256SUMS"
if ! curl -fL --proto '=https' --tlsv1.2 -o "$SUMS_PATH" "$SUMS_URL"; then
  err "failed to download SHA256SUMS from $SUMS_URL"
  exit 1
fi

EXPECTED="$(grep " ${ASSET}\$" "$SUMS_PATH" | awk '{print $1}' | head -n1)"
if [ -z "$EXPECTED" ]; then
  # Some sums files use "*" prefix for binary mode; try a looser match.
  EXPECTED="$(awk -v a="$ASSET" '$2 == a || $2 == "*" a {print $1; exit}' "$SUMS_PATH")"
fi
if [ -z "$EXPECTED" ]; then
  err "no SHA256 entry for $ASSET in SHA256SUMS"
  exit 1
fi

ACTUAL="$(sha256_of "$ASSET_PATH")"
if [ "$EXPECTED" != "$ACTUAL" ]; then
  err "SHA-256 mismatch for $ASSET"
  err "  expected: $EXPECTED"
  err "  actual:   $ACTUAL"
  exit 1
fi
ok "Checksum verified."

mkdir -p "$OPENSWE_INSTALL_DIR"
chmod 0755 "$OPENSWE_INSTALL_DIR"
chmod +x "$ASSET_PATH"
mv "$ASSET_PATH" "$OPENSWE_INSTALL_DIR/openswe"

INSTALL_PATH="$OPENSWE_INSTALL_DIR/openswe"
ok "Installed openswe -> $INSTALL_PATH"

case ":${PATH:-}:" in
  *":$OPENSWE_INSTALL_DIR:"*)
    info "$OPENSWE_INSTALL_DIR is already in your PATH."
    ;;
  *)
    printf "\n"
    warn "Add $OPENSWE_INSTALL_DIR to your PATH. For example:"
    printf "  echo 'export PATH=\"%s:\$PATH\"' >> ~/.bashrc   # or ~/.zshrc\n" "$OPENSWE_INSTALL_DIR"
    ;;
esac

printf "\n"
ok "Run \`openswe --help\` to get started."
