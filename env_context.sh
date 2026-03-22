#!/usr/bin/env bash

set -e

echo "=== OPEN-SWE ENVIRONMENT CONTEXT ==="

# --- Paths ---
AGENT_DIR="$HOME/ai/open-swe"
REPO_DIR="$HOME/ai/sandbox/demo_repo"
VENV_DIR="$AGENT_DIR/venv"
AGENT_FILE="$AGENT_DIR/local_fix_agent.py"

# --- Model ---
OLLAMA_URL="http://127.0.0.1:11434"
MODEL="qwen3-coder:30b"

echo
echo "📁 Paths:"
echo "Agent dir: $AGENT_DIR"
echo "Repo dir:  $REPO_DIR"
echo "Venv:      $VENV_DIR"
echo "Agent:     $AGENT_FILE"

# --- Check agent ---
echo
echo "🧠 Agent check:"
if [ -f "$AGENT_FILE" ]; then
  echo "✔ Agent file exists"
else
  echo "✖ Agent file missing"
fi

# --- Venv ---
echo
echo "🐍 Virtual environment:"
if [ -d "$VENV_DIR" ]; then
  echo "✔ Venv found"
  source "$VENV_DIR/bin/activate"
  echo "✔ Activated"
  python --version
else
  echo "✖ Venv missing"
fi

# --- Repo ---
echo
echo "📦 Repo check:"
if [ -d "$REPO_DIR" ]; then
  echo "✔ Repo exists"
  cd "$REPO_DIR"

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "✔ Git repo"
    echo "Branch: $(git branch --show-current)"
  else
    echo "⚠ Not a git repo"
  fi
else
  echo "✖ Repo missing"
fi

# --- Pytest ---
echo
echo "🧪 Pytest check:"
if command -v pytest >/dev/null 2>&1; then
  echo "✔ Pytest installed"
else
  echo "✖ Pytest NOT found"
fi

# --- Ollama ---
echo
echo "🤖 Ollama check:"
if curl -s "$OLLAMA_URL/api/tags" >/dev/null; then
  echo "✔ Ollama reachable"
else
  echo "✖ Ollama NOT reachable"
fi

# --- Model ---
echo
echo "🧠 Model check:"
if ollama list | grep -q "$MODEL"; then
  echo "✔ Model available: $MODEL"
else
  echo "⚠ Model NOT found locally"
fi

# --- Summary block for ChatGPT ---
echo
echo "================ COPY BELOW INTO NEW CHAT ================"
echo

cat <<EOF
Agent path:
$AGENT_FILE

Repo path:
$REPO_DIR

Venv:
$VENV_DIR

Run command:
cd $AGENT_DIR
python local_fix_agent.py --repo $REPO_DIR --test-cmd "pytest -q"

Model:
$MODEL via Ollama

Ollama endpoint:
$OLLAMA_URL/v1

Agent features:
- tool-based agent
- git tools
- branch-per-run
- critique loop
- pseudo-tool-call salvage

Goal:
Continue upgrading this agent (next step: safe auto-commit on test pass)
EOF

echo
echo "========================================================="
echo
