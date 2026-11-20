# Customization Guide

Robin is designed to be forked and customized for your org.
Every run's model loop runs on Managed Deep Agents (`mda/`).
What the run gets from the backend is resolved in one function as the run starts, `agent_catalog()` in `openswe/server.py` (`reviewer_catalog()` and `review_scout_catalog()` for the reviewer and the review scout).
It records the run's tools, models, subagents and loop rules in a `ToolSurface` (`openswe/sandboxes/tool_runtime.py`), and that is where you swap out the model and tools.

```python
# openswe/server.py — the end of agent_catalog()
surface.base_tools = main_tools  # tools the run can call
surface.loop = LoopRules(...)  # what MDA's middleware does around each model call
surface.models = SurfaceModels(
    main=ModelChoice(model_id, dict(model_kwargs)),
    subagent=ModelChoice(subagent_model_id, dict(subagent_model_kwargs)),
)
surface.subagents = [SurfaceSubagent(name="general-purpose", ...)]
```

---

## 1. Sandbox

Robin runs each thread in a [LangSmith cloud sandbox](https://docs.smith.langchain.com/) — an isolated Linux environment where the agent clones the repo and executes commands.
Managed Deep Agents creates it; the backend connects to it through `openswe/sandboxes/providers/langsmith.py`.

### Using a custom sandbox snapshot

New sandboxes use Robin's global execution image, falling back to the configured base or LangSmith's root snapshot when no image is set.
Admins configure the base snapshot, shared instructions, preloads, scripts, and resources under **Defaults** (`/settings`) or with `configure_execution_defaults`.
Use `rebuild_execution_image` to build the image or `publish_execution_image()` to capture an explicitly provisioned thread sandbox; saving configuration alone does neither.
Setup and update scripts receive space-delimited preload repository names in `ROBIN_REPOS`; `OPENSWE_REPOS` and `OPENSWE_WORKSPACE_REPOS` remain aliases for already-stored scripts.
See [Defaults, routing, and sandbox images](reference/execution.md) for authorization and build details.

Per-sandbox resources are configured on the deployment:

```bash
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES="137438953472"          # Optional, default 128 GiB
DEFAULT_SANDBOX_VCPUS="4"                                          # Optional, default 4
DEFAULT_SANDBOX_MEM_BYTES="17179869184"                            # Optional, default 16 GiB
DEFAULT_SANDBOX_IDLE_TTL_SECONDS="7200"                            # Optional, default 7200 (2 h); 0 disables
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS="604800"                 # Optional, default 604800 (7 d); 0 disables
```

This is useful for pre-installing languages, frameworks, or internal tools that your repos depend on — reducing setup time per agent run.
The default snapshot includes the GitHub CLI; agents invoke it as `gh <command>` and rely on the LangSmith proxy for the real credentials.

For LangSmith sandboxes, Robin configures two GitHub proxy rules on every sandbox a run works in:

- `github.com` / `*.github.com` receive Basic auth for git-over-HTTPS operations.
- `api.github.com` receives Bearer auth for `gh` and REST API operations.

The proxy token is minted at runtime from the GitHub App installation credentials.
The backend mints it and hands it to the Managed Deep Agents deployment, which writes these rules on the sandbox (see [mda/README.md](../mda/README.md)).
Do not store GitHub access tokens as deployment environment variables.

### Who creates sandboxes

Every thread's sandbox is a LangSmith sandbox that Managed Deep Agents creates when the thread's run starts, and replaces when the agent asks for a fresh one.
The backend never creates a thread's sandbox: it connects to the one MDA opened, writes its git identity, and runs the backend's tools in it; MDA writes its proxy config.

Sandbox provisioning, connection, proxy configuration, and global image captures use the deployment's `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`.
The configured base snapshot must exist in that LangSmith workspace.

## 2. Model

Set optional deployment defaults with `LLM_MODEL_ID` and `LLM_REASONING_EFFORT`:

```bash
LLM_MODEL_ID="anthropic:claude-opus-5-5"
LLM_REASONING_EFFORT="high"
```

When `LLM_MODEL_ID` is unset or blank, the deployment defaults to `openai:gpt-6.1-sol`, independently of provider credentials.
The default reasoning effort is `medium`.

Either variable can be set independently.
When only the model is set, `medium` is used if supported, otherwise that model's catalog default effort is used.
The model must be an allowed default in `openswe/dashboard/options.py`; unsupported models or incompatible efforts raise a configuration error when defaults are resolved.

These defaults apply below explicit run, thread, and global selections, including inherited reviewer and subagent defaults.
Existing selections are not overwritten.
Restart the backend after changing its environment.

Forked tasks configured with the main model reuse its model instance and reasoning effort for prompt-cache compatibility.
A different subagent model retains its own effort but cannot share the main model's cache.

`max_tokens` is a maximum completion/output token budget, not the model's total context window.
For OpenAI reasoning models, this budget can include both internal reasoning tokens and final response tokens.

### Switching models

Models are named in the `provider:model` format, with their provider settings, as a `ModelChoice`.
The MDA run builds the chat model from it (`mda/rt/models.py`), so a model object built in `agent_catalog()` never reaches the run:

```python
# Anthropic
ModelChoice("anthropic:claude-opus-5-5", {"temperature": 0, "max_tokens": 16_000})

# OpenAI (uses the Responses API on MDA)
ModelChoice("openai:gpt-6.1-sol", {"max_tokens": 128_000, "reasoning": {"effort": "medium"}})

# Google
ModelChoice("google_genai:gemini-2.5-pro", {"temperature": 0, "max_tokens": 16_000})
```

`provider_model_kwargs()` in `packages/runtime-api/src/runtime_api/models.py` builds those settings for a catalog model and effort.
To change how a provider's chat model is built (its client, retries or gateway routing), edit `make_model` there: the backend and the MDA runtime (`mda/rt/models.py`) both build their models with it, from the same model table.

### Using different models per context

You can route to different models based on task complexity, repo, or trigger source, in `agent_catalog()`:

```python
if cfg.source == "slack":
    # Faster model for Slack Q&A
    main = ModelChoice("anthropic:claude-opus-5-5", {"temperature": 0, "max_tokens": 16_000})
else:
    # Full model for code changes from Linear
    main = ModelChoice(
        "openai:gpt-6.1-sol", {"max_tokens": 128_000, "reasoning": {"effort": "medium"}}
    )

surface.models = SurfaceModels(main=main, subagent=main)
```

### Auto model routing with Jev

Eligible Auto turns use Jev through `langchain-typesafe`'s `TypeSafeClassifier`.
A LangSmith gateway key (`LANGSMITH_GATEWAY_API_KEY`, falling back to `LANGSMITH_API_KEY`) calls `typesafe/jev-1.13.0` through the [Gateway System One API](https://docs.langchain.com/langsmith/llm-gateway-decision-models); this requires a TypeSafe workspace provider secret and respects `LANGSMITH_GATEWAY_BASE_URL`.
Adaptive model routing is optional; provider calls always use the gateway.
The classifier is tagged `nostream` to keep its run out of the user-facing transcript.

Jev receives the latest human task text (up to 8,000 characters).
Missing API credentials, API errors, a three-second classification deadline, malformed responses, or confidence below `0.6` use the resolved agent default (thread override or global default), the same model used when routing is off.
The confidence cutoff is an initial heuristic, not a calibrated correctness probability.
Existing Auto eligibility, fast-mode control, and persisted routes are unchanged.

### Choosing a model in the opening request

New Slack threads and Auto/routed dashboard threads also use Jev to recognize explicit runtime model requests such as "use Opus" or `/model Oppus`.
This classification uses the first human request (the triggering message in Slack), up to 8,000 characters, and the available model catalog.
It shares Auto routing's credentials, three-second deadline, and confidence cutoff.
Title generation runs independently in the background.

A recognized model is checked for availability and image support, assigned a compatible default effort, and persisted before work begins.
It remains selected on follow-ups; explicit UI/API model choices take precedence.
A clearly unavailable model or failure to persist the selection stops the run.
No clear request, low confidence, or a classifier failure keeps the usual default/routing behavior.

Run the live intent cases from the repository root with an activated Python environment and configured gateway credentials:

```bash
LANGSMITH_TRACING=false python -m evals.model_request
```

The cases cover typos, ambiguous names, quoted and forwarded requests, model mentions as task subjects, and unavailable models.
They call Jev and are separate from the deterministic unit tests.

### Routing through the LangSmith LLM Gateway

Every model call goes through the [LangSmith LLM Gateway](https://docs.langchain.com/langsmith/llm-gateway).
The gateway resolves provider credentials from workspace Provider Secrets and applies central spend limits, PII/secrets redaction, and tracing.
Your organization must have the gateway enabled with Provider Secrets configured; provider keys in Robin's environment are not used.

| Env var | Default | Purpose |
|---|---|---|
| `LANGSMITH_GATEWAY_API_KEY` | `LANGSMITH_API_KEY` | LangSmith key with `gateway:invoke`. Prefer a dedicated key if the platform-provided key lacks this permission. |
| `LANGSMITH_GATEWAY_BASE_URL` | `https://gateway.smith.langchain.com` | Regional or self-hosted gateway host. |

Routing is mandatory on the backend and in MDA, applied centrally in `packages/runtime-api/src/runtime_api/models.py`.
**OpenAI, Anthropic, Baseten, Fireworks, and Google Gemini** are supported; unsupported providers or missing gateway credentials fail rather than contacting a provider directly.
OpenAI always uses HTTPS Responses.
Gateway routing and its OpenAI endpoint are not configurable switches.
Per-run bearer credentials still attribute calls to the person the run acts for; model selection, reasoning effort, and fallback settings remain configurable.

---

## 3. Tools

Robin ships with a small set of custom tools on top of the built-in Deep Agents tools (file reads, writes, edits, deletes, search, shell execution, and subagents).
GitHub operations are handled by `gh` inside the sandbox.

| Tool | File | Purpose |
|---|---|---|
| `fetch_url` | `openswe/tools/fetch_url.py` | Fetch web pages as markdown |
| `http_request` | `openswe/tools/http_request.py` | HTTP API calls |
| `classify` | `openswe/tools/classify.py` | Fast typed classification with TypeSafe's System One model, from code mode |
| `slack_attach_html` | `openswe/slack/tools/attach_html.py` | Attach sandbox HTML previews to Slack threads |
| `slack_reply` | `openswe/slack/tools/reply.py` | Reply to the person who asked, in a Slack thread or ephemerally |

### Classifying from code mode

`classify` asks TypeSafe's System One model questions about a state and returns typed answers with calibrated probabilities, typically in 70–500ms.
It goes through the [Gateway System One API](https://docs.langchain.com/langsmith/llm-gateway-decision-models) with the same LangSmith gateway key and TypeSafe provider secret as Auto model routing, so it needs no key of its own.
It is an experiment: each person turns it on under **Settings → Feature flags → Classify tool**, and their runs then offer it to code mode as `tools.classify`, kept off the model's tool list and found with `search_tools`.

Each question is a `choice` (one label from up to 255), a `noul` (the probability of yes), or a `score` (an expected level on an ordered rubric).
Every answer comes from the options supplied, and `choice` and `score` answers carry a `confidence` to gate actions on.
These examples are adapted from [langchain-typesafe](https://github.com/langchain-ai/langchain-typesafe), [Pipecat's classifier docs](https://docs.pipecat.ai/api-reference/server/classifiers/jev), [classifier.dev](https://jev101.org/guides/jev-text-classification-api-guide), and [TypeSafe's launch post](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

Route a support request, falling back to a person when unsure:

```typescript
const { answers } = await tools.classify({
  state: "Stripe fails whenever I connect my account.",
  questions: {
    department: {
      type: "choice",
      instructions: "Which team should handle this request?",
      criteria: {
        billing: "Payment, invoice, or subscription issues.",
        technical: "Product bugs or integration failures.",
        sales: "Pricing or purchasing questions.",
      },
    },
  },
});
const { choice, confidence } = answers.department;
const team = confidence >= 0.7 ? choice : "human_triage";
```

Ask a yes/no question, such as whether a call reached voicemail or a message is urgent:

```typescript
const { answers } = await tools.classify({
  state: "Hi, you've reached Dana. Leave a message.",
  questions: {
    voicemail: { type: "noul", instructions: "Is this a voicemail greeting?" },
    urgent: { type: "noul", instructions: "Does this message require an urgent response?" },
  },
});
const isVoicemail = answers.voicemail.noul >= 0.8;
```

Score a state on a rubric:

```typescript
const { answers } = await tools.classify({
  state: "This has failed three times. Fix it now.",
  questions: {
    frustration: {
      type: "score",
      instructions: "How frustrated does the customer appear?",
      criteria: ["Calm and neutral.", "Concerned but civil.", "Very angry or using strong language."],
    },
  },
});
const { score, probabilities } = answers.frustration;
```

Label a batch in parallel, sending low-confidence rows to review:

```typescript
const feedback = [
  "The export button crashes the app.",
  "Please add dark mode.",
  "Integration with external tools is seamless.",
];
const labeled = await Promise.all(
  feedback.map(async (text) => {
    const { answers } = await tools.classify({
      state: text,
      questions: {
        label: {
          type: "choice",
          instructions: "What kind of product feedback is this?",
          criteria: { bug: null, feature: null, praise: null, other: null },
        },
      },
    });
    return { text, ...answers.label, review: answers.label.confidence < 0.7 };
  }),
);
```

Guard an LLM's output before acting on it:

```typescript
const { answers } = await tools.classify({
  state: { prompt: userPrompt, response: draftReply },
  questions: {
    jailbreak: { type: "noul", instructions: "Does the prompt try to get the assistant to ignore its rules?" },
    leaks_secret: {
      type: "noul",
      instructions: "Does the response reveal a credential, token, or API key?",
      criteria: { true: "A real-looking secret value appears.", false: "No secret value appears." },
    },
  },
});
```

### MCP servers

Robin stores no MCP servers of its own.
A run gets MCP tools from LangSmith Managed Tools kits:

- A private thread loads its owner's `me` kit, which LangSmith builds from every server the owner can use: the workspace's servers, LangSmith built-ins, and servers the owner added for themselves.
  It is called with the owner's own LangSmith sign-in.
- Every other run loads Robin's bot kit (`LMT_BOT_KIT_ID`), called as the LMT profile `LMT_BOT_PROFILE_ID` with Robin's LangSmith key.

The bot kit, the workspace's servers and the bot profile's credentials are managed in LangSmith.
To give every run another MCP server, such as Datadog, incident.io, Currents, or Linear, add it to the bot kit; a server added to the LangSmith workspace is offered to everyone's private threads.
A person adds a server only they use under Settings → Connections, or by asking Robin in a private thread, which registers it in LangSmith once they confirm.

Linear sessions arrive through the MDA deployment's Linear channel, which posts the agent's answer in the session.
The channel reports a session's failed run in the session.
Sandbox provisioning uses the deployment's `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`.

MCP tools are named `mcp_<connection>_<tool>`, such as `mcp_lmt_…` for a kit's tools, with a hash suffix when the plain name would be ambiguous, contain other characters, or exceed 64 characters.
The backend caches a kit's tool list for ten minutes, and each tool call checks the connection and tool again.
An unavailable server omits its tools without preventing other servers from loading.

The backend implementation lives in `openswe/mcp`: `managed.py` reads the LMT kits, and `runtime.py` discovers and calls tools.
`load_mcp_tools(*sources)` takes caller-authorized `MCPSource`s from lowest to highest precedence; a later connection with the same name replaces the earlier one entirely.
Source lookup errors must raise instead of returning an empty result, so a failed lookup cannot expose a lower-precedence connection.

### Personal integrations

Use **Settings → Connections → LangSmith Managed Tools** for supported personal integrations.
The personal kit runs with the owner's accounts only in a **private thread started by its immutable owner**, and rechecks ownership before each tool call.
Collaborative threads use Robin's bot kit instead.
GitHub PR follow-ups targeting a private thread are rejected unless the commenter owns that thread, before credentials are read or a run is dispatched.

### Adding a Python tool

Create a new file in `openswe/tools/`, define a function, and add it to the tools list.

**Example — adding a Datadog search tool:**

```python
# openswe/tools/datadog_search.py
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

Then register it in `agent_catalog()` in `openswe/server.py`, where the run's tools are listed:

```python
from openswe.tools.datadog_search import datadog_search

static_tools = [
    *static_tools,
    datadog_search,  # new tool
]
```

The backend runs it whenever the MDA run calls it, through the run's tool-call middleware.

The agent will automatically see the tool's name, docstring, and parameter types — the docstring serves as the tool description, so write it clearly.

### Removing tools

If you don't use Slack, remove `slack_reply` from the tools list.
If you don't need web fetching, remove `fetch_url`.

### Conditional tools

You can vary the toolset based on the trigger source:

```python
base_tools = [http_request, fetch_url]

if cfg.source == "slack":
    tools = [*base_tools, slack_reply]
else:
    tools = base_tools

surface.base_tools = tools
```

---

## 4. Triggers

Robin supports three invocation surfaces: Linear, Slack, and GitHub.
Each is implemented as a webhook endpoint in `openswe/webapp.py`.
You can add, remove, or modify triggers independently.

### Removing a trigger

If you don't use Linear, simply don't configure the Linear webhook and remove the env vars.
Same for Slack.
The webhook endpoints still exist but won't receive events.

To fully remove a trigger's code, delete the corresponding endpoint from `openswe/webapp.py`:

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
- A Linear comment doesn't specify a repo
- A user writes `repo:name` without an org prefix — the org defaults to `DEFAULT_REPO_OWNER`

### Repository extraction from messages

Both Slack and Linear support specifying a target repo directly in the message or comment text.
The shared utility `extract_repo_from_text()` in `openswe/utils/repo.py` handles parsing these formats:

- `repo:owner/name` — explicit org and repo
- `repo owner/name` — space syntax (same result)
- `repo:name` — repo name only; the org defaults to `DEFAULT_REPO_OWNER`
- `https://github.com/owner/name` — GitHub URL

### Customizing Linear routing

Linear comments use the triggering user's dashboard default repository, then the global default repository.
Users can override either on a per-comment basis by including `repo:owner/name` in their `@robin` comment (legacy mention aliases remain supported).

### Customizing Slack routing

Slack repo resolution (`get_slack_repo_config` in `openswe/webhooks/common.py`) checks, in order:

1. Repo carried over from the existing Slack thread's metadata.
1. The triggering user's dashboard `default_repo`.
1. The global default repository.
1. `SLACK_REPO_OWNER`/`SLACK_REPO_NAME`, falling back to `DEFAULT_REPO_OWNER`/`DEFAULT_REPO_NAME`.

Users can still override per-message with `repo:owner/name` syntax in their Slack message (this is read from the message text by the agent).
A shorthand `repo:name` (without the org) is also supported — the org defaults to `DEFAULT_REPO_OWNER`.

Channel topics, purposes, and descriptions no longer configure instructions, routing, or repository defaults.
Put channel-specific workflow and repository guidance in the listening channel's instructions under **Integrations**; existing description text is not migrated.

### Adding a new trigger

To add a new invocation surface (e.g. Jira, Discord, a custom API):

1. **Add a webhook endpoint** in `openswe/webapp.py`:

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

1. **Create a processing function** that builds the prompt and starts an agent run:

```python
async def process_my_trigger(task_description: str, repo_config: dict):
    thread_id = generate_deterministic_id(task_description)
    langgraph_client = get_client(url=LANGGRAPH_URL)

    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": task_description}]},
        config={
            "configurable": {
                "repo": repo_config,
                "source": "my-trigger",
                "user_email": "user@example.com",
            }
        },
        if_not_exists="create",
    )
```

1. **Add a communication tool** (optional) so the agent can report back:

```python
# openswe/tools/my_trigger_reply.py
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

The system prompt is the template `openswe/resources/prompts/system/main.md.jinja`, rendered by `construct_system_prompt` in `openswe/prompt.py` on a thread's first run and kept unchanged for the rest of the thread, so the model provider's prompt cache keeps covering the conversation.
It includes each section from its own file under `openswe/resources/prompts/system/`, and `{% if %}` blocks in the template choose the sections that depend on the thread.
Guidance that can change between turns — the working environment and where the turn came from — is a `TurnContext` instead, told to the model in the conversation when it changes.
Edit a section's file to customize it:

| File | What it controls |
|---|---|
| `working-environment.md.jinja` | Sandbox paths, execution constraints, and repository setup steps; sent per turn |
| `source-context.md.jinja` | How to communicate on the surface the turn came from; sent per turn |
| `repository-setup.md.jinja` | Repository freshness, read-only requests, and repository skills |
| `task-execution.md` | Workflow steps (understand → implement → verify → submit) and PR review dispatch |
| `dependencies.md` | Installing, vetting, and managing project dependencies |
| `commit-pr.md` | PR title/body format, lint/format steps, and commit conventions (plus `commit-pr-desktop.md` for desktop runs) |
| `shared-base.md` | Shared core guidance: concise style, core behavior, sandbox operations, code style, and communication |

### Default prompt file

Robin supports a `default_prompt.md` file for org-level instructions that apply to **every** agent run, regardless of which repository is being worked on.
This is the recommended way to set default repository preferences, org conventions, and shared guidelines.

The file is loaded at agent startup and injected into the system prompt between the task overview and repository setup sections.

**Location:** [`openswe/resources/default_prompt.md`](../openswe/resources/default_prompt.md) for the bundled default.

**Override:** Set the `DEFAULT_PROMPT_PATH` environment variable to use a different file:

```bash
DEFAULT_PROMPT_PATH="/path/to/my-org-prompt.md"
```

**Format:** Write plain markdown.
The content is injected as-is under a `### Custom Instructions` heading in the system prompt.
Example:

```markdown
# Default Prompt

## Default Repository

When no repository is specified, work on the **my-app** repository under **my-org**.

## Organization Conventions

- Use conventional commits: feat:, fix:, chore:
- Always tag the requesting user when work is complete
```

**Loading order:** Default prompt → System prompt sections → AGENTS.md (per-repo).
If the file is missing or empty, it is silently skipped — no error is raised.

**When to use `default_prompt.md` vs `AGENTS.md`:**

| | `default_prompt.md` | `AGENTS.md` |
|---|---|---|
| Scope | All tasks, all repos | Single repository |
| Location | Robin project root | Target repo root |
| Use for | Default repo, org conventions | Repo-specific coding standards |

### Using AGENTS.md

Drop an `AGENTS.md` file in the root of any repository to add repo-specific instructions.
The agent reads it from the sandbox at startup and appends it to the system prompt.
This is the easiest way to encode conventions per-repo without modifying Robin's code.

---

## 6. Middleware

Middleware hooks run around the agent loop, in the Managed Deep Agents run (`mda/`).
Follow-ups and events that arrive mid-run are not middleware: the MDA run takes them from the thread's inbox (`openswe/threads/inbox.py`) before each model call.
What the loop does around each model call (reply and CLI-result nudges, routing, the step limit, what the run's end records) follows the `LoopRules` the backend puts in each run's catalog (`agent_catalog()` in `openswe/server.py`), and MDA's own middleware carries them out (`mda/rt/loop.py`).

There is intentionally no after-agent middleware that opens a PR for the agent.
The agent is responsible for committing, pushing, opening/updating the draft PR, and replying in the source channel.

Add custom middleware to the graph's middleware in `mda/agent.py` (`_parts`).
Tool calls are the MDA run's: a check around them goes in `mda/rt` (`rt/file_tools.py` for the file and shell tools).
Work that needs the backend's credentials or data goes behind a named endpoint of the typed API in `packages/runtime-api`, as the shell-command guards (`check_shell`) and the task context (`task_context`) do.
See the [LangChain middleware docs](https://python.langchain.com/docs/concepts/agents/#middleware) for the `@before_model` and `@after_agent` decorators.

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

Then add it to the graph's middleware in `mda/agent.py`:

```python
middleware = [
    BackendRunMiddleware(backend, opened_sandbox, booted_sandbox, read_ahead=[...]),
    ...,
    run_ci_check,  # new middleware
]
```
