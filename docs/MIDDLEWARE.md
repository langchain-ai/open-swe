# Middleware Guide

Middleware in Open SWE provides hooks into the agent execution lifecycle, allowing you to intercept, modify, and extend agent behavior without modifying core logic. This guide covers everything you need to understand and extend the middleware system.

## Table of Contents

- [Concepts](#concepts)
- [Middleware Types](#middleware-types)
- [Built-in Middleware](#built-in-middleware)
- [Execution Order](#execution-order)
- [Creating Custom Middleware](#creating-custom-middleware)
- [Best Practices](#best-practices)
- [Examples](#examples)

---

## Concepts

Middleware is a powerful pattern that lets you inject custom logic at key points in the agent's execution loop:

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent Execution Loop                     │
│                                                              │
│  ┌──────────────┐                                           │
│  │ User Input   │                                           │
│  └──────┬───────┘                                           │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────┐                                        │
│  │ BEFORE MODEL     │  ← @before_model middleware           │
│  │ (Pre-processing) │                                       │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐                                        │
│  │ MODEL CALL       │  LLM generates response               │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐                                        │
│  │ AFTER MODEL       │  ← @after_model middleware           │
│  │ (Post-processing)│                                       │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐                                        │
│  │ TOOL EXECUTION    │  Execute tool calls                   │
│  │ (with wrapping)  │  ← AgentMiddleware.wrap_tool_call     │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐                                        │
│  │ AFTER AGENT       │  ← @after_agent middleware           │
│  │ (Final cleanup)  │  Runs once after loop ends            │
│  └──────────────────┘                                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Why Middleware?

Middleware enables you to:

1. **Intercept and modify messages** before they reach the model
2. **Handle errors gracefully** without crashing agent runs
3. **Enforce invariants** (e.g., "always open a PR when done")
4. **Inject external context** (e.g., queued messages, user input)
5. **Add safety nets** for critical operations
6. **Log and monitor** agent behavior

---

## Middleware Types

Open SWE supports four types of middleware, each hooking into different parts of the execution cycle:

### 1. `@before_model` — Pre-Model Hook

Runs before each LLM call. Use for:
- Injecting new messages into the conversation
- Modifying state before the model sees it
- Checking external queues or state

```python
from langchain.agents.middleware import AgentState, before_model
from langgraph.runtime import Runtime

@before_model(state_schema=AgentState)
async def my_before_model(state: AgentState, runtime: Runtime) -> dict | None:
    """Runs before each model call."""
    # Return state updates, or None for no changes
    return {"messages": [{"role": "user", "content": "Additional context"}]}
```

### 2. `@after_model` — Post-Model Hook

Runs after each LLM call. Use for:
- Validating model output
- Ensuring the model made progress
- Injecting additional tool calls

```python
from langchain.agents.middleware import AgentState, after_model
from langgraph.runtime import Runtime

@after_model
def my_after_model(state: AgentState, runtime: Runtime) -> dict | None:
    """Runs after each model call."""
    last_message = state["messages"][-1]
    # Check if model made a tool call or generated content
    if not last_message.tool_calls and not last_message.text():
        # Force a retry
        return {"messages": [...]}
    return None
```

### 3. `@after_agent` — Agent Completion Hook

Runs once after the agent finishes (typically when the model returns without tool calls). Use for:
- Cleanup operations
- Safety nets (committing changes, opening PRs)
- Final notifications

```python
from langchain.agents.middleware import AgentState, after_agent
from langgraph.runtime import Runtime

@after_agent
async def my_after_agent(state: AgentState, runtime: Runtime) -> dict | None:
    """Runs after agent finishes."""
    # Perform final operations
    await send_notification("Agent completed!")
    return None
```

### 4. `AgentMiddleware` — Tool Call Wrapper

Wraps every tool call. Use for:
- Error handling and recovery
- Logging and monitoring
- Retry logic

```python
from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from collections.abc import Awaitable, Callable

class MyToolMiddleware(AgentMiddleware):
    """Wrap all tool calls with custom logic."""
    
    state_schema = AgentState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        """Synchronous tool call wrapper."""
        try:
            result = handler(request)
            # Log successful tool call
            print(f"Tool {request.tool_name} completed")
            return result
        except Exception as e:
            # Handle error
            return ToolMessage(
                content=f"Error: {e}",
                tool_call_id=request.tool_call.get("id"),
                status="error",
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        """Async tool call wrapper."""
        try:
            return await handler(request)
        except Exception as e:
            return ToolMessage(
                content=f"Error: {e}",
                tool_call_id=request.tool_call.get("id"),
                status="error",
            )
```

---

## Built-in Middleware

Open SWE includes four middleware components by default:

### 1. `ToolErrorMiddleware`

**Type:** `AgentMiddleware` (tool wrapper)

**Purpose:** Catches exceptions during tool execution and converts them to error `ToolMessage`s, allowing the LLM to see failures and self-correct.

**Location:** `agent/middleware/tool_error_handler.py`

```python
# Without this middleware:
# Tool error → Agent run crashes

# With this middleware:
# Tool error → ToolMessage(status="error", content="{...error details...}")
# → LLM sees the error and can retry or adapt
```

**Error Payload Format:**
```python
{
    "error": "Division by zero",
    "error_type": "ZeroDivisionError",
    "status": "error",
    "name": "calculate"  # tool name, if available
}
```

### 2. `check_message_queue_before_model`

**Type:** `@before_model`

**Purpose:** Injects follow-up messages (e.g., Linear comments, Slack messages) that arrived while the agent was busy. Enables real-time interaction.

**Location:** `agent/middleware/check_message_queue.py`

**Flow:**
1. Checks LangGraph store for pending messages for this thread
2. Extracts message content (text + images)
3. Injects as new user message into state
4. Clears the queue

```python
# User sends follow-up comment while agent is working
# → Message stored in queue
# → Before next model call, middleware injects it
# → Agent sees new context immediately
```

### 3. `ensure_no_empty_msg`

**Type:** `@after_model`

**Purpose:** Prevents empty model responses from causing issues. If the model returns nothing, it injects a `no_op` or `confirming_completion` tool call to ensure progress.

**Location:** `agent/middleware/ensure_no_empty_msg.py`

**Logic:**
1. If model returns empty content with no tool calls:
   - If the model already opened a PR or messaged the user → OK
   - Otherwise, inject a `no_op` tool call to force continuation
2. If model returns content but no tool calls (task might be incomplete):
   - Check if PR was opened or user was messaged
   - If not, inject `confirming_completion` to verify intent

```python
# Prevents: Model returns nothing → Agent hangs
# Ensures: Always make progress or explicitly end
```

### 4. `open_pr_if_needed`

**Type:** `@after_agent`

**Purpose:** Safety net that commits changes and opens a PR if the agent didn't explicitly do so. Ensures work is never lost.

**Location:** `agent/middleware/open_pr.py`

**Flow:**
1. Checks if `commit_and_open_pr` was called
2. If not, checks for uncommitted changes or unpushed commits
3. If changes exist:
   - Configures git user
   - Commits all changes
   - Pushes to feature branch
   - Opens/updates GitHub PR

```python
# Agent finishes but forgot to commit
# → Middleware catches it
# → Changes are committed and PR opened
# → No lost work
```

---

## Execution Order

Middleware runs in the order specified in the `middleware` list. Order matters!

```python
# agent/server.py
return create_deep_agent(
    middleware=[
        ToolErrorMiddleware(),           # 1. Wraps all tool calls
        check_message_queue_before_model, # 2. Before each model call
        ensure_no_empty_msg,              # 3. After each model call
        open_pr_if_needed,                # 4. After agent finishes
    ],
)
```

**Execution flow for each agent step:**

1. `check_message_queue_before_model` runs
2. Model is called
3. `ensure_no_empty_msg` runs
4. If model made tool calls:
   - Each tool call is wrapped by `ToolErrorMiddleware`
5. Loop continues or ends
6. When loop ends: `open_pr_if_needed` runs

**Order considerations:**

- `ToolErrorMiddleware` should typically be first to catch all tool errors
- `@before_model` middlewares run in listed order
- `@after_model` middlewares run in listed order
- `@after_agent` runs once at the end

---

## Creating Custom Middleware

### Example 1: Logging Middleware

Log every model call with timing:

```python
import time
import logging
from langchain.agents.middleware import AgentState, before_model, after_model
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

@before_model(state_schema=AgentState)
async def log_before_model(state: AgentState, runtime: Runtime) -> None:
    """Log before each model call."""
    state.setdefault("_timing", {})["model_start"] = time.time()
    logger.info("Model call starting with %d messages", len(state["messages"]))
    return None

@after_model
async def log_after_model(state: AgentState, runtime: Runtime) -> None:
    """Log after each model call."""
    timing = state.get("_timing", {})
    start = timing.get("model_start", time.time())
    duration = time.time() - start
    last_msg = state["messages"][-1]
    logger.info(
        "Model call completed in %.2fs, tool_calls=%d, content_len=%d",
        duration,
        len(last_msg.tool_calls),
        len(last_msg.text()),
    )
    return None
```

### Example 2: Rate Limiting Middleware

Enforce a maximum number of turns:

```python
from langchain.agents.middleware import AgentState, before_model
from langgraph.runtime import Runtime
from langchain_core.messages import HumanMessage

MAX_TURNS = 100

@before_model(state_schema=AgentState)
def enforce_turn_limit(state: AgentState, runtime: Runtime) -> dict | None:
    """Stop agent after MAX_TURNS to prevent runaway loops."""
    turn_count = state.get("_turn_count", 0)
    
    if turn_count >= MAX_TURNS:
        return {
            "messages": [HumanMessage(
                content=f"You have reached the maximum of {MAX_TURNS} turns. "
                "Please wrap up immediately and open a PR if you have changes."
            )]
        }
    
    return {"_turn_count": turn_count + 1}
```

### Example 3: CI Check Middleware

Run CI checks after the agent finishes:

```python
import asyncio
from langchain.agents.middleware import AgentState, after_agent
from langgraph.runtime import Runtime
from langgraph.config import get_config

from ..utils.sandbox_state import get_sandbox_backend
from ..utils.sandbox_paths import aresolve_repo_dir

@after_agent
async def run_ci_checks(state: AgentState, runtime: Runtime) -> dict | None:
    """Run CI checks after agent completes."""
    config = get_config()
    thread_id = config["configurable"].get("thread_id")
    repo_config = config["configurable"].get("repo", {})
    repo_name = repo_config.get("name")
    
    if not thread_id or not repo_name:
        return None
    
    sandbox_backend = await get_sandbox_backend(thread_id)
    if not sandbox_backend:
        return None
    
    repo_dir = await aresolve_repo_dir(sandbox_backend, repo_name)
    
    # Run tests
    result = await asyncio.to_thread(
        sandbox_backend.execute,
        f"cd {repo_dir} && npm test 2>&1 || pytest 2>&1 || echo 'No tests found'"
    )
    
    # Post results to PR or log
    print(f"CI results:\n{result.output}")
    
    return None
```

### Example 4: Context Injection Middleware

Inject external context before each model call:

```python
from langchain.agents.middleware import AgentState, before_model
from langgraph.runtime import Runtime
from langgraph.config import get_config
from datetime import datetime

@before_model(state_schema=AgentState)
async def inject_context(state: AgentState, runtime: Runtime) -> dict | None:
    """Inject current time and other context."""
    config = get_config()
    thread_id = config["configurable"].get("thread_id", "unknown")
    
    context_message = {
        "role": "system",
        "content": f"""
Current time: {datetime.now().isoformat()}
Thread ID: {thread_id}

Remember to check for queued messages and respond to user feedback.
        """.strip()
    }
    
    # Only inject every 5th turn to avoid noise
    turn_count = state.get("_context_turn", 0)
    if turn_count % 5 == 0:
        return {
            "messages": [context_message],
            "_context_turn": turn_count + 1,
        }
    
    return {"_context_turn": turn_count + 1}
```

### Example 5: Notification Middleware

Send notifications when the agent finishes:

```python
import httpx
from langchain.agents.middleware import AgentState, after_agent
from langgraph.runtime import Runtime
from langgraph.config import get_config

WEBHOOK_URL = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

@after_agent
async def send_completion_notification(state: AgentState, runtime: Runtime) -> None:
    """Send Slack notification when agent finishes."""
    config = get_config()
    thread_id = config["configurable"].get("thread_id")
    repo = config["configurable"].get("repo", {})
    
    # Find PR link if opened
    messages = state.get("messages", [])
    pr_url = None
    for msg in reversed(messages):
        content = getattr(msg, "content", "")
        if "github.com" in content and "/pull/" in content:
            # Extract PR URL (simplified)
            pr_url = content
            break
    
    payload = {
        "text": f"Open SWE task completed",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Task Completed*\n"
                    f"Thread: `{thread_id}`\n"
                    f"Repo: `{repo.get('owner', '?')}/{repo.get('name', '?')}`\n"
                    f"PR: {pr_url or 'Not opened'}"
                }
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        await client.post(WEBHOOK_URL, json=payload)
    
    return None
```

---

## Best Practices

### 1. Keep Middleware Focused

Each middleware should do one thing well:

```python
# ✅ Good: Single responsibility
@before_model
def inject_timestamp(state, runtime):
    return {"current_time": time.time()}

# ❌ Bad: Too many responsibilities
@before_model
def do_everything(state, runtime):
    # Injects time, checks queue, validates state, sends alerts...
    pass
```

### 2. Handle Errors Gracefully

Middleware should never crash the agent:

```python
@before_model
async def safe_middleware(state: AgentState, runtime: Runtime) -> dict | None:
    try:
        # Your logic here
        return {"key": "value"}
    except Exception as e:
        logger.exception("Middleware error (non-fatal)")
        return None  # Don't crash, just skip
```

### 3. Return None for No-Op

Return `None` when you don't want to modify state:

```python
@before_model
def conditional_middleware(state: AgentState, runtime: Runtime) -> dict | None:
    if some_condition:
        return {"messages": [...]}  # Modify state
    return None  # No changes needed
```

### 4. Use Type Hints

Type hints improve IDE support and catch errors:

```python
from typing import Any
from langchain.agents.middleware import AgentState
from langgraph.runtime import Runtime

@before_model(state_schema=AgentState)
async def typed_middleware(
    state: AgentState, 
    runtime: Runtime
) -> dict[str, Any] | None:
    ...
```

### 5. Log Appropriately

Use debug/info/warning/error levels appropriately:

```python
import logging

logger = logging.getLogger(__name__)

@before_model
async def logging_middleware(state: AgentState, runtime: Runtime) -> None:
    logger.debug("Detailed state: %s", state.keys())
    logger.info("Processing message %d", len(state["messages"]))
    logger.warning("Unusual condition detected")
    logger.error("Something went wrong (but not crashing)")
    return None
```

### 6. Test Middleware Independently

Write unit tests for your middleware:

```python
import pytest
from langchain_core.messages import HumanMessage, AIMessage

@pytest.mark.asyncio
async def test_my_middleware():
    state = {
        "messages": [HumanMessage(content="Hello")],
    }
    runtime = MockRuntime()  # You'll need to mock this
    
    result = await my_middleware(state, runtime)
    
    assert result is not None
    assert "messages" in result
```

### 7. Consider Performance

Middleware runs on every iteration. Avoid heavy operations:

```python
# ❌ Bad: Expensive operation on every call
@before_model
async def expensive_middleware(state, runtime):
    data = await fetch_large_dataset()  # Don't do this every iteration
    return {"data": data}

# ✅ Better: Cache or check conditions first
@before_model
async def efficient_middleware(state, runtime):
    if state.get("_data_cached"):
        return None  # Already have data
    
    if state.get("_turn_count", 0) % 10 == 0:  # Only every 10 turns
        data = await fetch_large_dataset()
        return {"_data_cached": data}
    
    return None
```

---

## Adding Middleware to Your Agent

Middleware is configured in `agent/server.py` in the `get_agent()` function:

```python
from .middleware import (
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    open_pr_if_needed,
)
from .middleware.my_custom_middleware import my_custom_middleware

async def get_agent(config: RunnableConfig) -> Pregel:
    return create_deep_agent(
        model=make_model("anthropic:claude-opus-4-6", ...),
        system_prompt=construct_system_prompt(...),
        tools=[...],
        backend=sandbox_backend,
        middleware=[
            ToolErrorMiddleware(),           # Tool error handling
            check_message_queue_before_model, # Message injection
            ensure_no_empty_msg,              # Progress enforcement
            open_pr_if_needed,                # PR safety net
            my_custom_middleware,             # Your custom middleware
        ],
    )
```

### Conditional Middleware

You can vary middleware based on configuration:

```python
async def get_agent(config: RunnableConfig) -> Pregel:
    base_middleware = [
        ToolErrorMiddleware(),
        check_message_queue_before_model,
        ensure_no_empty_msg,
    ]
    
    # Add CI checks only for production
    if config["configurable"].get("environment") == "production":
        base_middleware.append(run_ci_checks)
    
    # Always add PR opener last (safety net)
    base_middleware.append(open_pr_if_needed)
    
    return create_deep_agent(
        middleware=base_middleware,
        ...
    )
```

---

## Further Reading

- [LangChain Middleware Documentation](https://python.langchain.com/docs/concepts/agents/#middleware)
- [LangGraph Runtime Documentation](https://langchain-ai.github.io/langgraph/)
- [Deep Agents Framework](https://github.com/langchain-ai/deepagents)
- [CUSTOMIZATION.md](CUSTOMIZATION.md) - Full customization guide