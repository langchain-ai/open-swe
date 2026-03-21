#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
REPO_DIR="$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_launchers.sh [--bin-dir DIR] [--repo DIR]

Installs user-level launchers:
  fixapp      -> python local_fix_agent.py --interactive
  fixpublish  -> ./scripts/fixpublish.sh
  fixit       -> python local_fix_agent.py

Options:
  --bin-dir DIR   Install directory. Default: ~/.local/bin
  --repo DIR      Default repo path baked into the launchers.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bin-dir)
      BIN_DIR="$2"
      shift 2
      ;;
    --repo)
      REPO_DIR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$BIN_DIR"

write_launcher() {
  local target_path="$1"
  local launcher_mode="$2"
  cat >"$target_path" <<EOF
#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO=$(printf '%q' "$REPO_DIR")

resolve_repo() {
  if [[ -n "\${OPEN_SWE_REPO:-}" ]]; then
    printf '%s\n' "\$OPEN_SWE_REPO"
    return
  fi
  if [[ -n "\${LOCAL_FIX_AGENT_REPO:-}" ]]; then
    printf '%s\n' "\$LOCAL_FIX_AGENT_REPO"
    return
  fi
  if [[ -f "\$PWD/local_fix_agent.py" && -d "\$PWD/scripts" ]]; then
    printf '%s\n' "\$PWD"
    return
  fi
  printf '%s\n' "\$DEFAULT_REPO"
}

REPO_DIR="\$(resolve_repo)"
if [[ ! -f "\$REPO_DIR/local_fix_agent.py" ]]; then
  echo "Launcher could not find local_fix_agent.py in repo: \$REPO_DIR" >&2
  exit 1
fi

cd "\$REPO_DIR"
case "$launcher_mode" in
  fixapp)
    exec "\${PYTHON:-python}" local_fix_agent.py --interactive "\$@"
    ;;
  fixpublish)
    exec ./scripts/fixpublish.sh "\$@"
    ;;
  fixit)
    exec "\${PYTHON:-python}" local_fix_agent.py "\$@"
    ;;
  *)
    echo "Unknown launcher mode: $launcher_mode" >&2
    exit 2
    ;;
esac
EOF
  chmod +x "$target_path"
}

write_launcher "$BIN_DIR/fixapp" "fixapp"
write_launcher "$BIN_DIR/fixpublish" "fixpublish"
write_launcher "$BIN_DIR/fixit" "fixit"

echo "Installed launchers in: $BIN_DIR"
echo "Default repo path: $REPO_DIR"
echo "Launchers:"
echo "- fixapp -> python local_fix_agent.py --interactive"
echo "- fixpublish -> ./scripts/fixpublish.sh"
echo "- fixit -> python local_fix_agent.py"

case ":${PATH:-}:" in
  *":$BIN_DIR:"*)
    echo "PATH status: $BIN_DIR is already on PATH"
    ;;
  *)
    echo "PATH status: $BIN_DIR is not on PATH"
    echo "Run this now:"
    echo "export PATH=\"$BIN_DIR:\$PATH\""
    echo "Persist it in your shell profile:"
    echo "echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc"
    echo "echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc"
    ;;
esac
