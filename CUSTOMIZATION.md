# Customization Guide

Open SWE is designed to be forked and customized for your org. The core agent is assembled in a single function — `get_agent()` in `agent/server.py` — where you can swap out the sandbox, model, tools, and triggers.

```python
# agent/server.py — the key lines
return create_deep_agent(
    model=make_model("anthropic:claude-opus-4-6", temperature=0, max_tokens=20_000),
    system_prompt=construct_system_prompt(repo_dir, ...),
    tools=[http_request, fetch_url, commit_and_open_pr, linear_comment, slack_thread_reply, github_comment],
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

The `deepagents` ecosystem includes several sandbox providers out of the box. To swap providers, replace the `create_langsmith_sandbox()` call in `agent/server.py` with one of the following:

#### Modal

```bash
pip install langchain-modal
```

```python
import modal
from langchain_modal import ModalSandbox

app = modal.App.lookup("open-swe")
sandbox_backend = ModalSandbox(sandbox=modal.Sandbox.create(app=app))
```

This is what Ramp uses for their Inspect agent — container-based isolation with fast spin-up.

#### Daytona

```bash
pip install langchain-daytona
```

```python
from daytona import Daytona
from langchain_daytona import DaytonaSandbox

sandbox = Daytona().create()
sandbox_backend = DaytonaSandbox(sandbox=sandbox)
```

#### Runloop

```bash
pip install langchain-runloop
```

```python
import os
from runloop_api_client import RunloopSDK
from langchain_runloop import RunloopSandbox

client = RunloopSDK(bearer_token=os.environ["RUNLOOP_API_KEY"])
devbox = client.devbox.create()
sandbox_backend = RunloopSandbox(devbox=devbox)
```

#### Local shell (no isolation — development only)

```python
from deepagents.backends import LocalShellBackend

sandbox_backend = LocalShellBackend(
    root_dir="/path/to/repo",
    inherit_env=True,
)
```

> **Warning**: `LocalShellBackend` runs commands directly on your host machine with no sandboxing. Only use for local development with human-in-the-loop enabled.

#### Wiring it up

All providers implement `SandboxBackendProtocol` and are interchangeable. Replace the sandbox creation in `agent/server.py`:

```python
# Before (LangSmith)
sandbox_backend = await asyncio.to_thread(create_langsmith_sandbox)

# After (any provider)
sandbox_backend = await asyncio.to_thread(create_my_sandbox)
```

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

Open SWE ships with six custom tools on top of the built-in Deep Agents tools (file operations, shell execution, subagents, todos):

| Tool | File | Purpose |
|---|---|---|
| `commit_and_open_pr` | `agent/tools/commit_and_open_pr.py` | Git commit + GitHub draft PR |
| `fetch_url` | `agent/tools/fetch_url.py` | Fetch web pages as markdown |
| `http_request` | `agent/tools/http_request.py` | HTTP API calls |
| `linear_comment` | `agent/tools/linear_comment.py` | Post comments on Linear tickets |
| `slack_thread_reply` | `agent/tools/slack_thread_reply.py` | Reply in Slack threads |
| `github_comment` | `agent/tools/github_comment.py` | Post comments on GitHub issues/PRs |

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

### Customizing Slack routing

Slack uses env vars for default routing:

```bash
SLACK_REPO_OWNER="my-org"
SLACK_REPO_NAME="my-repo"
```

Users can override per-message with `repo:owner/name` syntax in their Slack message.

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
