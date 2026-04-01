---
name: building-langgraph-agents
description: >-
  open-swe agent patterns: create_deep_agent, sync tools with get_config(),
  middleware types, LangGraph Store. Use when creating or modifying agents,
  tools, or middleware.
user-invocable: false
---

# Agent Patterns

**Ask before making changes. Explain your plan, wait for approval.**

## Agent Creation
Use `create_deep_agent` from `deepagents` — NEVER `StateGraph`. Default model: `anthropic:claude-opus-4-6`. Recursion limit: `1_000`. Pass middleware via `create_deep_agent(..., middleware=[...])`.

## Tools
Sync `def` (not async). Use `get_config()` for thread context. Return `{"success": bool, "error": str | None}`. Wrap in `try/except` — return error dict, never raise. Use `asyncio.run()` for async helpers. Register in `agent/server.py` + `agent/tools/__init__.py`.

## Config Access
```python
config = get_config()  # from langgraph.config
thread_id = config["configurable"].get("thread_id")
repo = config["configurable"].get("repo", {})  # {owner, name}
metadata = config.get("metadata", {})  # branch_name, github_token_encrypted, repo_dir
```

## Middleware
From `langchain.agents.middleware`:
- `@before_model(state_schema=AgentState)` — async, runs before each LLM call, returns dict or None
- `@after_agent` — async, runs once after agent finishes
- `AgentMiddleware` subclass — `wrap_tool_call` / `awrap_tool_call` wraps individual tool calls

Register in `agent/middleware/__init__.py` + `agent/server.py`.

## Store
`get_store()` for persistent cross-turn state. `await store.aget(namespace, key)` / `await store.adelete(namespace, key)`.

## Async Bridge
Sync tool → async: `asyncio.run(fn())`. Async middleware → sync: `await asyncio.to_thread(fn)`.
