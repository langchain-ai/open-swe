# Customization Guide

Open SWE is designed to be forked and customized for your org. The core agent is assembled in a single function — `get_agent()` in `agent/server.py` — where you can swap out the sandbox, model, tools, and triggers.

```python
# agent/server.py — the key lines
return create_deep_agent(
    model=make_model("anthropic:claude-opus-4-6", temperature=0, max_tokens=20_000),
    system_prompt=construct_system_prompt(repo_dir, ...),
    tools=[http_request, fetch_url, commit_and_open_pr, linear_comment, slack_thread_reply],
    backend=sandbox_backend,
    middleware=[
        ToolErrorMiddleware(),
        check_message_queue_before_model,
        ensure_no_empty_msg,
        open_pr_if_needed,
    ],
)
```

---

## 1. Sandbox

By default, Open SWE runs each task in a [LangSmith cloud sandbox](https://docs.smith.langchain.com/) — an isolated Linux environment where the agent clones the repo and executes commands. Sandbox creation and connection is handled in `agent/integrations/langsmith.py`.

### Using a custom sandbox template

Set environment variables to use a custom Docker image:

```bash
DEFAULT_SANDBOX_TEMPLATE_NAME="my-template"    # Template registered in LangSmith
DEFAULT_SANDBOX_TEMPLATE_IMAGE="my-org/my-image:latest"  # Docker image
```

This is useful for pre-installing languages, frameworks, or internal tools that your repos depend on — reducing setup time per agent run.

### Using a different sandbox provider

Set the `SANDBOX_TYPE` environment variable to switch providers. Each provider has a corresponding integration file in `agent/integrations/` and a factory function registered in `agent/utils/sandbox.py`:

| `SANDBOX_TYPE` | Integration file | Required env vars |
|---|---|---|
| `langsmith` (default) | `agent/integrations/langsmith.py` | `LANGSMITH_API_KEY_PROD`, `SANDBOX_TYPE="langsmith"` |
| `daytona` | `agent/integrations/daytona.py` | `DAYTONA_API_KEY`, `SANDBOX_TYPE="daytona"` |
| `runloop` | `agent/integrations/runloop.py` | `RUNLOOP_API_KEY`, `SANDBOX_TYPE="runloop"` |
| `modal` | `agent/integrations/modal.py` | Modal credentials, `SANDBOX_TYPE="modal"` |
| `local` | `agent/integrations/local.py` | None (no isolation — development only), `SANDBOX_TYPE="local"` |

> **Warning**: `local` runs commands directly on your host with no sandboxing. Only use for local development with human-in-the-loop enabled.

### Adding a new sandbox provider

1. **Create an integration file** at `agent/integrations/my_provider.py` with a factory function matching this signature:

```python
def create_my_provider_sandbox(sandbox_id: str | None = None):
    """Create or reconnect to a sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
            If None, creates a new sandbox.

    Returns:
        An object implementing SandboxBackendProtocol.
    """
    ...
```

2. **Register it** in `agent/utils/sandbox.py` by importing your factory and adding it to `SANDBOX_FACTORIES`:

```python
from agent.integrations.my_provider import create_my_provider_sandbox

SANDBOX_FACTORIES = {
    ...
    "my_provider": create_my_provider_sandbox,
}
```

The factory must return an object implementing `SandboxBackendProtocol` from `deepagents`. See the existing integration files for reference.

### Building a custom sandbox provider

If none of the built-in providers fit, you can build your own. The agent accepts any backend that implements `SandboxBackendProtocol` from `deepagents`. The protocol requires:

- **File operations**: `ls_info()`, `read()`, `write()`, `edit()`, `glob_info()`, `grep_raw()`
- **Shell execution**: `execute(command, timeout=None) -> ExecuteResponse`
- **Identity**: `id` property returning a unique sandbox identifier

The easiest approach is to extend `BaseSandbox` from `deepagents.backends.sandbox` — it implements all file operations by delegating to `execute()`, so you only need to implement the shell execution layer:

```python
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse

class MySandbox(BaseSandbox):
    def __init__(self, connection):
        self._conn = connection

    @property
    def id(self) -> str:
        return self._conn.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = self._conn.run(command, timeout=timeout or 300)
        return ExecuteResponse(
            output=result.stdout + result.stderr,
            exit_code=result.exit_code,
            truncated=False,
        )
```

See `agent/integrations/langsmith.py` (`LangSmithBackend` class) for a full reference implementation.

---

## 2. Model

The model is configured in the `get_agent()` function in `agent/server.py`:

```python
model=make_model("anthropic:claude-opus-4-6", temperature=0, max_tokens=20_000)
```

### Switching models

Use the `provider:model` format:

```python
# Anthropic
model=make_model("anthropic:claude-sonnet-4-6", temperature=0, max_tokens=16_000)

# OpenAI (uses Responses API by default)
model=make_model("openai:gpt-4o", temperature=0, max_tokens=16_000)

# Google
model=make_model("google_genai:gemini-2.5-pro", temperature=0, max_tokens=16_000)
```

The `make_model()` helper in `agent/utils/model.py` wraps `langchain.chat_models.init_chat_model`. For OpenAI models, it automatically enables the Responses API. For full control, pass a pre-configured model instance directly:

```python
from langchain_anthropic import ChatAnthropic

model = ChatAnthropic(model_name="claude-sonnet-4-6", temperature=0, max_tokens=16_000)

return create_deep_agent(
    model=model,
    ...
)
```

### Using different models per context

You can route to different models based on task complexity, repo, or trigger source:

```python
async def get_agent(config: RunnableConfig) -> Pregel:
    source = config["configurable"].get("source")
    
    if source == "slack":
        # Faster model for Slack Q&A
        model = make_model("anthropic:claude-sonnet-4-6", temperature=0, max_tokens=16_000)
    else:
        # Full model for code changes from Linear
        model = make_model("anthropic:claude-opus-4-6", temperature=0, max_tokens=20_000)
    
    return create_deep_agent(model=model, ...)
```

---

## 3. Tools

Open SWE ships with five custom tools on top of the built-in Deep Agents tools (file operations, shell execution, subagents, todos):

| Tool | File | Purpose |
|---|---|---|
| `commit_and_open_pr` | `agent/tools/commit_and_open_pr.py` | Git commit + GitHub draft PR |
| `fetch_url` | `agent/tools/fetch_url.py` | Fetch web pages as markdown |
| `http_request` | `agent/tools/http_request.py` | HTTP API calls |
| `linear_comment` | `agent/tools/linear_comment.py` | Post comments on Linear tickets |
| `slack_thread_reply` | `agent/tools/slack_thread_reply.py` | Reply in Slack threads |

### Adding a tool

Create a new file in `agent/tools/`, define a function, and add it to the tools list.

**Example — adding a Datadog search tool:**

```python
# agent/tools/datadog_search.py
import requests
from typing import Any

def datadog_search(query: str, time_range: str = "1h") -> dict[str, Any]:
    """Search Datadog logs for debugging context.

    Args:
        query: Datadog log query string
        time_range: Time range to search (e.g. "1h", "24h", "7d")

    Returns:
        Dictionary with matching log entries
    """
    # Your Datadog API integration here
    ...
```

Then register it in `agent/server.py`:

```python
from .tools import commit_and_open_pr, fetch_url, http_request, linear_comment, slack_thread_reply
from .tools.datadog_search import datadog_search

return create_deep_agent(
    ...
    tools=[
        http_request, fetch_url, commit_and_open_pr,
        linear_comment, slack_thread_reply,
        datadog_search,  # new tool
    ],
    ...
)
```

The agent will automatically see the tool's name, docstring, and parameter types — the docstring serves as the tool description, so write it clearly.

### Removing tools

If you only use Linear (not Slack), remove `slack_thread_reply` from the tools list and vice versa. If you don't need web fetching, remove `fetch_url`. The only tool that's essential to the core workflow is `commit_and_open_pr`.

### Conditional tools

You can vary the toolset based on the trigger source:

```python
base_tools = [http_request, fetch_url, commit_and_open_pr]
source = config["configurable"].get("source")

if source == "linear":
    tools = [*base_tools, linear_comment]
elif source == "slack":
    tools = [*base_tools, slack_thread_reply]
else:
    tools = [*base_tools, linear_comment, slack_thread_reply]

return create_deep_agent(tools=tools, ...)
```

---

## 4. Triggers

Open SWE supports three invocation surfaces: Linear, Slack, and GitHub. Each is implemented as a webhook endpoint in `agent/webapp.py`. You can add, remove, or modify triggers independently.

### Removing a trigger

If you don't use Linear, simply don't configure the Linear webhook and remove the env vars. Same for Slack. The webhook endpoints still exist but won't receive events.

To fully remove a trigger's code, delete the corresponding endpoint from `agent/webapp.py`:

- **Linear**: `linear_webhook()` and `process_linear_issue()`
- **Slack**: `slack_webhook()` and `process_slack_mention()`

### Default repository

Set the default GitHub org and repo used across all triggers (Slack, Linear, GitHub) when no repo is specified:

```bash
DEFAULT_REPO_OWNER="my-org"      # Default GitHub org (used everywhere)
DEFAULT_REPO_NAME="my-repo"      # Default GitHub repo (used everywhere)
```

These are used as the fallback when:
- A Slack message doesn't specify a repo (and no thread metadata exists)
- A Linear issue's team/project isn't in the `LINEAR_TEAM_TO_REPO` mapping
- A user writes `repo:name` without an org prefix — the org defaults to `DEFAULT_REPO_OWNER`

### Repository extraction from messages

Both Slack and Linear support specifying a target repo directly in the message or comment text. The shared utility `extract_repo_from_text()` in `agent/utils/repo.py` handles parsing these formats:

- `repo:owner/name` — explicit org and repo
- `repo owner/name` — space syntax (same result)
- `repo:name` — repo name only; the org defaults to `DEFAULT_REPO_OWNER`
- `https://github.com/owner/name` — GitHub URL

### Customizing Linear routing

The `LINEAR_TEAM_TO_REPO` dict in `agent/utils/linear_team_repo_map.py` maps Linear teams and projects to GitHub repos:

```python
LINEAR_TEAM_TO_REPO = {
    "Engineering": {
        "projects": {
            "backend": {"owner": "my-org", "name": "backend"},
            "frontend": {"owner": "my-org", "name": "frontend"},
        },
        "default": {"owner": "my-org", "name": "monorepo"},
    },
}
```

Users can also override the team/project mapping on a per-comment basis by including `repo:owner/name` in their `@openswe` comment. This takes priority over the mapping — the mapping is used as a fallback when no repo is specified in the comment. If the team/project isn't found in the mapping either, `DEFAULT_REPO_OWNER`/`DEFAULT_REPO_NAME` is used.

### Customizing Slack routing

Slack uses `DEFAULT_REPO_OWNER` and `DEFAULT_REPO_NAME` as the fallback when no repo is specified in a message.

Users can override per-message with `repo:owner/name` syntax in their Slack message. A shorthand `repo:name` (without the org) is also supported — the org defaults to `DEFAULT_REPO_OWNER`.

### Adding a new trigger

To add a new invocation surface (e.g. Jira, Discord, a custom API):

1. **Add a webhook endpoint** in `agent/webapp.py`:

```python
@app.post("/webhooks/my-trigger")
async def my_trigger_webhook(request: Request, background_tasks: BackgroundTasks):
    # Parse the incoming event
    payload = await request.json()
    
    # Extract task description and repo info
    task_description = payload["description"]
    repo_config = {"owner": "my-org", "name": "my-repo"}
    
    # Create a LangGraph run
    background_tasks.add_task(process_my_trigger, task_description, repo_config)
    return {"status": "accepted"}
```

2. **Create a processing function** that builds the prompt and starts an agent run:

```python
async def process_my_trigger(task_description: str, repo_config: dict):
    thread_id = generate_deterministic_id(task_description)
    langgraph_client = get_client(url=LANGGRAPH_URL)
    
    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": task_description}]},
        config={"configurable": {
            "repo": repo_config,
            "source": "my-trigger",
            "user_email": "user@example.com",
        }},
        if_not_exists="create",
    )
```

3. **Add a communication tool** (optional) so the agent can report back:

```python
# agent/tools/my_trigger_reply.py
def my_trigger_reply(message: str) -> dict:
    """Post a reply to the triggering service."""
    # Your API call here
    ...
```

The key fields in `config.configurable` are:
- `repo`: `{"owner": "...", "name": "..."}` — which GitHub repo to work on
- `source`: string identifying the trigger (used for auth routing and communication)
- `user_email`: the triggering user's email (for GitHub OAuth resolution)

---

## 5. System prompt

The system prompt is assembled in `agent/prompt.py` from modular sections. You can customize behavior by editing individual sections:

| Section | What it controls |
|---|---|
| `WORKING_ENV_SECTION` | Sandbox paths and execution constraints |
| `TASK_EXECUTION_SECTION` | Workflow steps (understand → implement → verify → submit) |
| `CODING_STANDARDS_SECTION` | Code style, testing, and quality rules |
| `COMMIT_PR_SECTION` | PR title/body format and commit conventions |
| `CODE_REVIEW_GUIDELINES_SECTION` | How the agent reviews code changes |
| `COMMUNICATION_SECTION` | Formatting and messaging guidelines |

### Using AGENTS.md

Drop an `AGENTS.md` file in the root of any repository to add repo-specific instructions. The agent reads it from the sandbox at startup and appends it to the system prompt. This is the easiest way to encode conventions per-repo without modifying Open SWE's code.

---

## 6. Middleware

Middleware hooks run around the agent loop. Open SWE includes four:

| Middleware | Type | Purpose |
|---|---|---|
| `ToolErrorMiddleware` | Tool error handler | Catches and formats tool errors |
| `check_message_queue_before_model` | Before model | Injects follow-up messages that arrived mid-run |
| `ensure_no_empty_msg` | Before model | Prevents empty messages from reaching the model |
| `open_pr_if_needed` | After agent | Safety net — opens a PR if the agent didn't |

Add custom middleware by appending to the middleware list in `get_agent()`. See the [LangChain middleware docs](https://python.langchain.com/docs/concepts/agents/#middleware) for the `@before_model` and `@after_agent` decorators.

**Example — adding a CI check after agent completion:**

```python
from langchain.agents.middleware import AgentState, after_agent
from langgraph.runtime import Runtime

@after_agent
async def run_ci_check(state: AgentState, runtime: Runtime):
    """Run CI checks after the agent finishes."""
    # Trigger your CI pipeline here
    ...
```

Then add it to the middleware list:

```python
middleware=[
    ToolErrorMiddleware(),
    check_message_queue_before_model,
    ensure_no_empty_msg,
    open_pr_if_needed,
    run_ci_check,  # new middleware
],
```
