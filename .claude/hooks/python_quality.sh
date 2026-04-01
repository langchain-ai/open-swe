#!/usr/bin/env bash
set -euo pipefail

# Read tool input from stdin (JSON)
input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // .tool_input.filePath // empty')

# Only process Python files
if [[ -z "$file_path" ]] || [[ "$file_path" != *.py ]]; then
  exit 0
fi

# Resolve to absolute path if relative
if [[ "$file_path" != /* ]]; then
  file_path="$CLAUDE_PROJECT_DIR/$file_path"
fi

# Skip if file doesn't exist (was deleted)
if [[ ! -f "$file_path" ]]; then
  exit 0
fi

# Use venv tools if available, fall back to PATH
BLACK="${CLAUDE_PROJECT_DIR}/venv/bin/black"
RUFF="${CLAUDE_PROJECT_DIR}/venv/bin/ruff"
MYPY="${CLAUDE_PROJECT_DIR}/venv/bin/mypy"
PYTHON="${CLAUDE_PROJECT_DIR}/venv/bin/python"
[[ -x "$BLACK" ]] || BLACK="black"
[[ -x "$RUFF" ]] || RUFF="ruff"
[[ -x "$MYPY" ]] || MYPY="mypy"
[[ -x "$PYTHON" ]] || PYTHON="python3"

# Format with Black (silent — just fix it)
"$BLACK" --quiet --line-length=120 "$file_path" 2>/dev/null || true

# Auto-fix lint issues with Ruff (silent — just fix it)
"$RUFF" check --fix --quiet "$file_path" 2>/dev/null || true

# Check for remaining Ruff issues — surface these to Claude
ruff_output=$("$RUFF" check "$file_path" 2>&1) || {
  echo "$ruff_output" >&2
  echo "Ruff found issues in $file_path that need manual fixes" >&2
  exit 2
}

# Run mypy type checking on the edited file
mypy_output=$("$MYPY" "$file_path" 2>&1) || {
  echo "$mypy_output" >&2
  echo "mypy found type errors in $file_path" >&2
  exit 2
}

# Run project style checks on the edited file
style_output=$("$PYTHON" -c "
import sys
sys.path.insert(0, '$CLAUDE_PROJECT_DIR')
from pathlib import Path
from scripts.ci.check_style import check_file
errors = check_file(Path('$file_path'), Path('$CLAUDE_PROJECT_DIR'))
if errors:
    for e in errors:
        print(e)
    sys.exit(1)
" 2>&1) || {
  echo "$style_output" >&2
  echo "Style check found issues in $file_path" >&2
  exit 2
}

exit 0
