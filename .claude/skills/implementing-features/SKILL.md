---
name: implementing-features
description: >-
  End-to-end feature implementation workflow for open-swe. Use when building a new feature,
  adding a new tool, creating middleware, or making substantial code changes from a ticket
  or task screenshot. Enforces task focus, linting, and commit safety.
user-invocable: false
---

# Feature Implementation Workflow

Implement: $ARGUMENTS

## Integration with executing-plans

When the developer provides a task/ticket and you create a plan first, use the **executing-plans** skill to execute it step by step. Flow:

1. Developer gives task (screenshot, description, review comment)
2. You create a plan (list of steps, files to change, verification)
3. Switch to **executing-plans** skill to execute the plan with checkpoints

## STOP — Ask Before Acting

**NEVER start writing code immediately.** Always explain what you plan to do and wait for the developer's go-ahead before making any changes. Do not be eager — confirm first, act second.

## Step 0: Task Focus Lock

When the developer provides a screenshot or description of a ticket/task:

1. **Extract and remember** every detail: title, description, acceptance criteria, labels, assignee, priority, linked issues
2. **Restate the task** back to the developer in your own words to confirm understanding
3. **Scope lock**: Only implement what the ticket asks for. Do NOT refactor surrounding code, add extra features, improve docs, or "clean up" anything outside the task scope
4. **If tempted to change something outside scope**, stop and ask the developer first

## Step 1: Understand the Codebase

- Read relevant existing code in the affected area
- Determine which part of the codebase is affected:
  - `agent/tools/` — sync tool functions using `get_config()` for context
  - `agent/middleware/` — `@before_model`, `@after_agent`, or `AgentMiddleware` subclasses
  - `agent/utils/` — shared utilities (auth, sandbox, GitHub, Linear, Slack)
  - `agent/prompt.py` — system prompt construction
  - `agent/server.py` — agent creation via `create_deep_agent`, tool/middleware registration
- Identify existing patterns in neighboring files and follow them exactly

## Step 2: Plan

Before writing code, outline:
- Which files will be created or modified
- How this connects to the existing agent architecture (tools, middleware, config)
- Confirm the plan with the developer before proceeding

## Step 3: Implement

Follow open-swe conventions:

### Tools, Middleware & Config

Follow patterns from the **building-langgraph-agents** skill for:
- Tool creation (sync `def`, `get_config()`, error dict returns)
- Middleware (`@before_model`, `@after_agent`, `AgentMiddleware`)
- Config access (`configurable`, `metadata`)
- Register new tools in `agent/server.py` `get_agent()` and `agent/tools/__init__.py`
- Register new middleware in `agent/middleware/__init__.py` and `agent/server.py`

### Production Code Standards

Every line of code must be deployment-ready. This is NOT a local prototype — it runs in production sandboxes.

- **No hardcoded values**: No localhost URLs, local file paths, hardcoded tokens, or machine-specific paths. Use `os.environ.get()` with sensible defaults or config from `get_config()`
- **No debug leftovers**: No `print()` statements, no `breakpoint()`, no commented-out code, no `TODO` hacks. Use `logging.getLogger(__name__)` for all output
- **Proper error handling**: Every external call (GitHub API, sandbox commands, HTTP requests) must handle failures gracefully. Return structured error dicts, never let exceptions bubble up unhandled
- **No secrets in code**: Never hardcode tokens, API keys, or credentials. Access via encrypted config (`github_token_encrypted` in metadata) or environment variables
- **Idempotent operations**: Tools and middleware should be safe to retry. Don't assume state — check before mutating
- **Thread safety**: Use `get_config()` for thread-scoped state, never module-level mutable globals for per-request data
- **Structured logging**: Use `logger.info/warning/error/exception` with context (`thread_id`, `repo`, operation) — not bare strings

### General
- Agent creation uses `create_deep_agent` from `deepagents` — never construct StateGraph manually
- Sandbox commands via `SandboxBackendProtocol` from `agent.utils.sandbox_state`
- Add to package `__init__.py` and `__all__` if public

## Step 4: Lint, Verify & Complete

Follow the **executing-plans** skill for lint/verify/branch/commit steps. In short:
- Run `ruff check --fix` + `ruff format` on changed files
- Fix any issues from the PostToolUse hook (`python_quality.sh`)
- Suggest branch name and commit message — **never commit or push without developer permission**

## Reminders

- Stay within the task scope — do not touch unrelated code
- If something seems wrong outside the task, mention it but do not fix it
- Always ask before any git operation (commit, push, branch creation)
- If the task is ambiguous, ask clarifying questions before implementing
