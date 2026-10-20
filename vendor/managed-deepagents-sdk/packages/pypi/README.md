<!-- markdownlint-disable MD033 MD041 -->

<div align="center">
  <a href="https://docs.langchain.com/langsmith/python/managed-deep-agents-overview">
    <img alt="Managed Deep Agents logo" src="https://raw.githubusercontent.com/langchain-ai/managed-deepagents/main/logo.png" width="70%">
  </a>
</div>

<div align="center">
  <h3>Python authoring package and CLI launcher for Managed Deep Agents.</h3>
</div>

> [!IMPORTANT]
> **Public beta.** Managed Deep Agents is in public beta. The PyPI package API
> and managed runtime contract may change. See the
> [docs](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview)
> for getting started and updates.

`managed-deepagents` is the PyPI package for authoring Managed Deep Agents in
Python. It includes:

- `define_deep_agent`, the Python authoring contract for managed agents.
- `define_schedule`, the Python contract for managed cron schedules.
- `mda`, the CLI used to build and deploy your agent to LangSmith.
- `managed_deepagents.runtime`, the runtime helper used by generated managed
  entry modules.

## Install

```bash
uv tool install managed-deepagents
```

> [!NOTE]
> **Private beta: dev releases only.** We currently publish only PEP 440
> pre-release (dev) versions and no stable version yet. uv skips pre-releases
> by default unless they are allowed explicitly:
>
> ```bash
> uv tool install --prerelease allow managed-deepagents
> ```

This package requires Python 3.9 or newer. Each platform wheel bundles the
prebuilt `mda` binary for its OS and CPU architecture and exposes it through the
`mda` console script. This PyPI-installed CLI scaffolds and compiles Python
projects only and vendors the Python runtime bundled with this wheel.

To start a new project, run `mda init`. In a terminal, the CLI asks you to name
the agent:

```bash
mda init
```

Run `mda init -i` to initialize your agent interactively and optionally hand it
off to a coding agent to build:

```bash
mda init -i
```

Choose a coding agent to continue in the new project, or select **View raw
prompt** to copy the setup instructions.

For coding agents and other headless use, pass the project name:

```bash
mda init my-agent
```

`mda init` can shape the project up front — `--instructions "..."` (or
`--instructions-file <path>`) writes the system prompt, `--model <spec>` picks
the model, `--memory agent` opts into deployment-shared durable memory,
`--no-sandbox` leaves out the sandbox, and `-c slack` (also `--channel` /
`--channels`) writes `channels/slack.py`. Every new project includes an
`identity.py` that explicitly selects `auth.langsmith_api_key()` authentication.

## Evaluate

Managed Deep Agent evals run in Harbor. Install `uv` and Docker before running
them.

1. From the project root, initialize the eval workspace:

   ```bash
   mda evals init -i
   ```

2. Follow the coding-agent prompt to author tasks directly under
   `evals/<task>/`. The CLI creates the user-owned `evals/harbor-job.json` once
   and preserves later edits. `.mda/evals/` is generated.
   A task may include an authored `evals/<task>/identity.json` fixture. It
   requires a non-empty `user.id`; `user.kind`, `user.email`, and top-level
   `groups`, `claims`, and `source.provider` are optional. Keep it with the
   task, not in generated `.mda/evals/`.
3. Export `LANGSMITH_API_KEY`, `LANGSMITH_WORKSPACE_ID` when your credentials
   require it, and the model or tool credential variables used by the agent.
4. From the same project root, run the pinned Harbor 0.21.0 command included at
   the end of the coding-agent prompt. The command loads `MDAJobPlugin` and
   `LangSmithPlugin`. It uses POSIX syntax on macOS/Linux and PowerShell on
   native Windows.
5. From the same project root, inspect results:

   ```bash
   uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor view .mda/evals/jobs
   ```

`MDAJobPlugin` compiles a fresh eval artifact at every Harbor job start.
This POC keeps MDA's custom Harbor adapter; migration to Harbor's built-in
LangGraph agent is deferred.

## Define an Agent

Create an `agent.py` that defines an `agent`:

```python
from managed_deepagents import define_deep_agent

# The system prompt comes from instructions.md next to this file.
agent = define_deep_agent(
    name="research-assistant",
    model="openai:gpt-5.5",
    tools=[query_db],
)
```

`define_deep_agent` requires `name` and supports the [`create_deep_agent`](https://reference.langchain.com/python/deepagents/graph/create_deep_agent) options except the platform-owned `backend`, `store`, `checkpointer`, `system_prompt`, and `memory` fields. Static agents use `instructions.md` and all discovered skills.

Export a sync or async factory as `agent` to select configuration per run:

```python
from managed_deepagents import ManagedServerRuntime, define_deep_agent


def agent(runtime: ManagedServerRuntime):
    execution = runtime.execution_runtime
    ctx = execution.context if execution is not None else {}
    return define_deep_agent(
        name="support",
        model=(
            "openai:gpt-5-mini"
            if isinstance(ctx, dict) and ctx.get("tier") == "fast"
            else "openai:gpt-5.5"
        ),
    )
```

The factory can select tools, middleware, instructions, skills, MCP definitions,
a sandbox, and memory. Omitted selections use project resources. `None` disables
an instruction, skill, MCP, sandbox, or memory selection for that run. Empty
strings and arrays remain explicit selections. Select memory with `define_memory(...)`;
selected memory cannot use `allow`. Root `memory.py` remains the fallback.

On direct runs, `execution_runtime.context` is application context or `None`.
On channel runs, it is `None`; read delivery data from `runtime.channel`.
`ManagedServerRuntime[AppContext]` preserves the application's context type.
It extends the native `langgraph_sdk.runtime.ServerRuntime` variants with managed
fields. `access_context` identifies the Agent Server operation that called the
factory.
During inspection, `execution_runtime` is `None`. Keep the factory repeatable and
preserve graph structure and schemas. MDA skips managed resource reads and
allocation. Context is application data; tools must check authorization.

`mda build` reads the inline literal `name` without calling the factory. Install
all model integrations the factory can select.

To authenticate SDK and API requests with a LangSmith workspace key while
retaining MDA's thread and store authorization, declare it explicitly:

```python
identity = define_identity(auth=auth.langsmith_api_key())
```

Clients send the key as `x-api-key`. LangSmith Cloud supplies the verification
endpoint and tenant configuration; do not add those platform-owned values to
the project `.env`.

On deploy, Context Hub stores harness files (`instructions.md`, `skills/**`). A
root `memory.py` enables agent and user memory independently:

```python
from managed_deepagents import MemoryLayer, define_memory

memory = define_memory(
    agent=MemoryLayer(),
    user=MemoryLayer(),
)
```

Omit a layer to disable it. A layer with no `allow` callback uses its default
policy. Agent memory is shared across callers and is available by default.
User memory requires a trusted person and defaults to managed Slack one-to-one
DMs (the original provider event has `channel_type == "im"`). Shared
conversations, missing event data, and direct API runs get no user mount unless
an authored policy allows it.

For compatibility, an autodiscovered root declaration can still use a sync or
async `allow(context)` callback to replace its default policy. `allow` is
deprecated and emits a warning; prefer selecting memory from a dynamic agent:

```python
from typing import TypedDict
from managed_deepagents import ManagedChannelContext, MemoryLayer, define_memory


class Context(TypedDict):
    remember: bool


def allow_memory(context: Context | ManagedChannelContext) -> bool:
    return not isinstance(context, ManagedChannelContext) and context.get("remember") is True


memory = define_memory(
    agent=MemoryLayer(),
    user=MemoryLayer(allow=allow_memory),
)
```

Verified Studio users get the declared user memory layer without calling its
policy. Under `mda dev`, a personal LangSmith API key resolves the developer's
`langsmith-dev` Agent Auth principal; memory stays in `.mda/__contexthub__`.
Deployed Studio uses the verified LangSmith user ID and stores memory in Context Hub.

When evaluated, each callback runs once before memory mounts, including interrupt
resumes. Returning `false` removes only that layer's mount and hot memory for
the run. If the context cannot satisfy the policy schema, MDA denies that layer
without calling its policy. An error from the callback still fails the run.
User policies cannot grant access to another
person's memory or grant user memory to a service principal. Build, schema, and
state inspection do not call either policy.

The legacy `scope` option remains supported. Do not combine it with `agent` or
`user`.

User hot memory and agent hot memory with an `allow` policy are loaded for each
model call. They are not saved in thread state. This keeps the graph structure
stable and prevents a later denied run from loading saved memory. A missing
hot file stays empty until the first memory write.

The agent layer is mounted at `/memories/agent/`. The user layer requires
`identity.py` and is mounted from an opaque per-user Context Hub repo at
`/memories/user/`, keyed on the trusted caller principal. Each mount's
`AGENTS.md` is injected every turn; other files are read on demand. Deploy never
overwrites existing memories. A project without `memory.py` mounts no durable
memory.

## Async subagents

Mark a subagent with `"async": True` to run it in the background. MDA builds it
as its own graph in the same deployment, and the agent starts it, checks on it,
and reads its result while it keeps working:

```python
agent = define_deep_agent(
    name="lead",
    subagents=[
        {
            "name": "researcher",
            "description": "Researches a topic in the background",
            "system_prompt": "You research topics thoroughly.",
            "async": True,
        }
    ],
)
```

The subagent runs under the caller's identity and uses the project's sandbox,
identity, and connector declarations; its sandbox is separate from the agent's.
When it leaves `model`, `tools`, `permissions`, or `interrupt_on` unset, it uses
the agent's. It does not inherit the agent's middleware, skills, or memory.

When the subagent pauses for approval, the agent sees the task as waiting.
Answer the approval in the subagent's own thread, and the task continues.

Each async subagent is its own graph, so any caller allowed to start runs on
this deployment can also run it directly, as themselves. It doesn't get the
agent's middleware, so don't rely on the agent's middleware to guard the tools
it shares with a subagent.

Write async subagents in `agent.py` as dict literals, or names bound to one, so
the build can find them without running your code. `mda build` warns about
subagent entries it can't read. Async subagents are Python only, and are not
supported in `mda eval`.

## Run context and channels

The runtime separates application data, trusted caller information, and the
current channel delivery:

```text
runtime
  .context          // Application data on direct runs; None on channel runs
  .server_info      // Trusted caller and server information
  .channel          // Present for a managed channel delivery
    .name           // Configured channel name
    .provider       // Provider label, such as slack
    .event          // Typed MDA delivery; no internal routing
    .raw_event      // Optional original provider payload
    .post(...)      // Present when sending is supported
  .backend          // Present when a sandbox is configured
  .schedules        // Create and manage schedules for this run's caller
```

API callers supply application data through Agent Server's `context` parameter:

```python
await client.runs.create(
    thread_id,
    assistant_id,
    input={"messages": [{"role": "user", "content": "Hello"}]},
    context={"remember": True},
)
```

Direct API runs use your authored `context_schema` and native LangGraph context
validation. On channel runs, tools, middleware, and authored factories receive
`None` as context. Use `runtime.channel.event` for the typed event and
`runtime.channel.raw_event` for optional provider data. The application schema
stays on the graph, but its required fields do not apply to channel runs.
Supplying a `channel` field in a direct API call does not establish a trusted
channel source.

Agent Server passes the current delivery to MDA through its `context` input.
MDA resolves the verified channel before it calls the authored factory and
removes delivery data before native graph execution. MDA prepares memory access
in the graph factory for each run or resume. Direct callers of the managed
compiler must build a fresh graph with the execution's context; a compiled graph
is scoped to that execution.

`runtime.channel` describes the current verified delivery. It is absent on
ordinary API and schedule runs. Its facts are available even when the channel
cannot send. `post` uses a private, verified reply target. The channel helper
does not expose the target or let callers replace it.

```python
if runtime.channel is not None and runtime.channel.post is not None:
    await runtime.channel.post({"type": "content", "content": "I am checking that now."})
```

The built-in Slack `runtime.channel.post` sends text content through Trigger.
It rejects native payloads and content it cannot send. For channels that support
native messages, set `type` to `"native"` and supply the JSON in `native`.
For content messages, set `type` to `"content"` and supply `content`.
Supply only the matching payload. Native JSON has no extra provider or payload
wrapper. The `runtime.channel` helper has no `address`, `update`, or destination override.
A post sends an additional message; the normal final agent reply remains automatic.

Managed Slack channels save incoming files under `/workspace/attachments/` and
add `upload_files(paths)` so the agent can attach completed workspace files
to its reply. This requires a managed sandbox.
On each message, the runtime also checks up to five pages of Slack thread history
for files, requesting 100 messages per page. This requires the bot's history scope
for that conversation. Duplicate file IDs are processed once per run, and files
already saved at the attachment path are reused. If history is unavailable or the
scan limit is reached, the attachment status reports that files may be missing.

The event types are `message`, `user_prompt_response`, and `interrupt_resume`.
Message events contain `messages`. Prompt responses contain `action`, optional
`value`, and optional `correlation_id`. Resume events contain `resume`.
For Trigger Slack deliveries, `raw_event` is the inner provider event. It excludes
HTTP headers and the outer Trigger envelope. A resume can omit the raw event;
MDA does not reuse the previous delivery's event.

For a prompt that answers an interrupt, put a shared ID on the prompt and on the
interrupt value under `INTERRUPT_CORRELATION_KEY`. MDA uses `correlation_id` to
select that interrupt. Without a correlation ID, an answer can resume the only
pending interrupt; multiple pending interrupts require a match. Existing resume
values are unchanged.

The managed runtime fields are available to authored tools and middleware,
including declarative subagents, without an identity or sandbox declaration.
Channel projects still require an identity declaration for trusted ingress.
`server_info` preserves platform information and adds managed caller information
when available. Its `link.status` currently reports `not_required`; it does not
perform an account-link lookup. Its source thread identifier retains the existing
provider-thread/LangGraph-thread fallback behavior.

### Self-hosted channel runtime configuration

Agent Server's `MOUNT_PREFIX` is honored by managed event routes and default
loopback clients. Explicit `MDA_CHANNEL_API_URL` or `LANGGRAPH_API_URL` overrides
must already include the deployment path.

Trigger-backed channels require `LANGSMITH_MDA_TRIGGER_SERVER_URL` and a
deployment key (`LANGSMITH_API_KEY`, or `LANGCHAIN_API_KEY` on self-hosted).
The runtime prefers `LANGSMITH_API_KEY` when both are present.

If the Agent Auth token issuer differs from the runtime's LangSmith API URL,
set `OAUTH_AS_ISSUER` in the deployment environment to the configured issuer
(for example, `https://langsmith.example.com/api`). The runtime verifies the
exact issuer and fetches its public `/.well-known/jwks.json`; it does not trust
an issuer supplied by an incoming token. LangSmith must have signing keys
configured, and the agent must be able to reach that public-key endpoint.

## Tool and middleware runtime types

Python requires 3.11 or later and includes Deep Agents, LangChain, and LangGraph.
MCP provider packages remain optional.

Use `ManagedToolRuntime[Context, State]` for tools and tool-call hooks. Lifecycle
and model hooks keep the native `Runtime[Context]` signature. Use
`isinstance(runtime, ManagedRuntime)` to access MDA fields. Both preserve native
fields and your declared context type.
`ManagedDeepAgentRuntime` is an alias for the tool type. MDA adds `server_info`,
`channel`, `backend`, and `schedules`; channel and backend are optional.

## HTTP channels

Use `channels.http(provider=..., verify=..., parse=..., post=...)` for a
provider webhook. MDA has no built-in Slack HTTP adapter. Your application
supplies the adapter: `verify` checks the request signature, `parse` selects the
input message, and the optional async `post(message)` sends replies. The built-in
`channels.slack()` continues to use Trigger.

A message returned by `parse` contains `user_id`, `thread_id`, `content`, and
`target`. `thread_id` is the stable LangGraph thread UUID. `target` is separate
JSON that the adapter needs to reply, such as a Photon conversation ID or a Slack
channel and thread timestamp. MDA keeps this target private and binds it to
`runtime.channel.post`. The adapter's `post(message)` callback receives the
target for reply routing.

`verify` runs first with the original bytes and headers. `verify` and `parse`
each get a separately readable request body. `parse` owns body decoding and can
read JSON, form data, text, or bytes. It returns the existing `message` or `ignore`
result. MDA creates `runtime.channel.event` from the parsed message.

To expose provider data, return
`{"type": "message", "message": message, "raw_event": event}` from `parse`.
MDA validates this value as finite, acyclic JSON and copies it into
`runtime.channel.raw_event`. If you omit `raw_event`,
`runtime.channel.raw_event` is `None`. MDA does not decode the body again.
An explicit `None` is a valid JSON null.

HTTP and Slack Trigger runs expose delivery through `runtime.channel`.

The callback receives one `HttpChannelPostInput` dictionary:

```python
{"type": "content", "content": "The job is complete.", "target": {"conversation": "provider-thread"}}
{"type": "native", "native": {"blocks": []}, "target": {"conversation": "provider-thread"}}
```

It returns `HttpPostedMessage`, with a required `id` and optional `url`. Both types
are exported by `managed_deepagents`. The callback can create or reuse its provider
client directly; MDA does not call a client factory before accepting the webhook.
A callback error is a delivery failure. `content` is a portable message string or
content blocks. `native` is JSON for the provider, such as a Slack Block Kit
message. Supply exactly one form. A Slack adapter can use the provider's `text` field inside `native` as the
fallback for its blocks. That field is part of the Slack payload; it is not a
second MDA `content` field. The adapter validates native data and uses its bound
target and credentials when it calls the provider API. Native routing or
authentication fields must not replace them.

Automatic agent replies use the `content` form. An explicit
`await runtime.channel.post({"type": "native", "native": ...})` reaches your
adapter without conversion. If `post` is omitted, the channel can receive events
and exposes facts, but has no runtime `post` method. The webhook path is
`/channels/<name>/events`, where `<name>` is the channel file name. An existing
Photon webhook needs no URL change when the deployment host and channel name
stay the same.

If a run or its observation fails, MDA uses `post` to send “Something went wrong
while processing your request.” A failed send is logged without another attempt.
Optional async `on_error(error, target)` replaces this default. MDA supplies the
reply target; pass it to your adapter's `post` to send a custom error message.

## Project Shape

```text
my-agent/
  agent.py              # named `agent` variable
  identity.py           # managed authentication (included by `mda init`)
  memory.py             # optional durable-memory declaration
  instructions.md       # managed system prompt
  pyproject.toml
  .env                  # local deploy secrets, never committed
  schedules/            # optional managed cron schedules
  tools/                # optional custom tools and MCP declaration
    mcp.py              # optional MCP server declaration
  middleware/           # optional middleware
  skills/               # optional skills synced to Context Hub
  sandbox/              # LangSmith sandbox (`mda init` includes this; delete to opt out)
```

The CLI copies your project files into the managed build and generates the entry
module that connects your definition to the hosted runtime.

The agent entry must live at the project root as `agent.py`.

## Define a Schedule

Create one file per schedule under `schedules/` and define a named `schedule`:

```python
# schedules/daily_digest.py
from managed_deepagents import define_schedule

schedule = define_schedule(
    cron="0 8 * * 1-5",
    timezone="America/Los_Angeles",
    prompt="Write the daily digest.",
)
```

`mda deploy` reconciles schedules as LangSmith cron jobs after the deployment is
live. Declarations must be statically serializable literals or top-level
constants; prompt schedules become user-message input, and stateless runs clean
up their temporary thread after completion.

### Runtime schedules

Use `runtime.schedules` in a tool or middleware to create schedules while the
agent runs. Schedules act for the caller of the current run. Each schedule
belongs to the current user or to the agent. You can list, get, update, and
delete the schedules of an owner.

```python
from langchain.tools import tool
from managed_deepagents import ManagedToolRuntime


@tool
async def remind_me(
    prompt: str, cron: str, runtime: ManagedToolRuntime, timezone: str = "UTC"
) -> str:
    """Run a prompt for the current user on a cron schedule."""
    item = await runtime.schedules.create(
        owner={"type": "user"}, cron=cron, timezone=timezone, prompt=prompt
    )
    return f"Created schedule {item['id']}."
```

The other operations take the same owner:

```python
owner = {"type": "user"}
page = await runtime.schedules.list(owner=owner)
await runtime.schedules.update(item["id"], {"paused": True}, owner=owner)
await runtime.schedules.delete(item["id"], owner=owner)
```

To run a schedule once, give `at` in place of `cron`.

```python
from datetime import datetime, timedelta, timezone

await runtime.schedules.create(
    owner=owner,
    at=datetime.now(timezone.utc) + timedelta(hours=1),
    prompt="Remind me to check the deploy.",
)
```

Set `new_thread` to choose whether a run starts a new thread or continues the
current thread. When a schedule is created from a channel such as Slack,
each run posts its final answer to that channel. Runtime schedules stay when you deploy again.

Each owner can have at most 20 runtime schedules per agent, paused ones
included. Set `MDA_MAX_AGENT_SCHEDULES` or `MDA_MAX_USER_SCHEDULES` in the
deployment environment to change the limit for agent-owned or user-owned
schedules. Expired one-time schedules and schedules defined in code do not count. A run started by
a schedule can create, list, get, update, and delete schedules like any other run.

## Sandbox

`mda init` scaffolds `sandbox/__init__.py` with a LangSmith sandbox. MDA only
enables the sandbox when that declaration is present — delete `sandbox/` to opt
out:

```python
from managed_deepagents import define_sandbox

sandbox = define_sandbox(
    idle_ttl_seconds=600,
)
```

If `sandbox/setup.sh` exists, `mda deploy` / `mda dev` bake it into a recipe
snapshot once; thread sandboxes clone that snapshot and do not re-run setup.
MDA owns sandbox naming, image/snapshot selection, reuse, and lifecycle.

`setup.sh` is baked on the base in `sandbox/__init__.py`, or on the default
image when there is no declaration. A dynamic agent that selects a sandbox
gets the baked setup as long as its `define_sandbox(...)` sets no
`docker_image`, `snapshot_name`, or `snapshot_id`, or sets the same one. A run
that selects a different base fails, because `setup.sh` was never baked on it.

Authored tools and middleware can use the Deep Agents backend interface at
`runtime.backend`. It is `None` when the project has no sandbox:

```python
from managed_deepagents import ManagedToolRuntime


def write_report(runtime: ManagedToolRuntime) -> None:
    if runtime.backend is None:
        raise RuntimeError("This tool requires a sandbox")
    result = runtime.backend.write("/workspace/report.txt", "Report ready")
    if result.error:
        raise RuntimeError(result.error)
```

The backend uses the standard Deep Agents file arguments and results: `ls`,
`read`, `write`, `edit`, `grep`, and `glob`. `delete` is optional and depends on
the installed backend. Use `upload_files` and `download_files` for binary data.
Python also has async methods such as `aread`, `awrite`, and `adelete`.
All paths refer to the current sandbox. Context Hub routes for skills and
memory are not part of this backend.

Private published images can declare registry credentials by environment
variable name; MDA creates or updates the deployment-owned Host registry:

```python
sandbox = define_sandbox(
    docker_image="ghcr.io/acme/agent-base:1",
    registry={
        "url": "ghcr.io",
        "username": "octocat",
        "password_env": "GHCR_TOKEN",
    },
)
```

Put `GHCR_TOKEN` in the project `.env` or process environment. Its value is
used only to reconcile the registry and never enters the build or snapshot.

## MCP Servers

Add `tools/mcp.py` to attach MCP servers. The file must define a module-level
`mcp`. By default, MDA exposes every tool loaded from each
declared server. In `mda dev`, an agent-owned connection reads from
`MDA_DEV_<SLUG>` with the slug uppercased and hyphens changed to underscores.
Hosted deployments resolve workspace connections from Agent Auth:

The old `connectors/mcp.py` file API remains available with a warning during
`0.7.x`. It will be removed in `0.8.0`.

```python
from managed_deepagents import define_mcp, connections

mcp = define_mcp(
    servers={
        "langchainDocs": {
            "transport": "http",
            "url": "https://docs.langchain.com/mcp",
            "include_tools": ["search", "fetch"],
            "connection": connections.get("docs-token", {"type": "agent"}),
        },
        "exa": {
            "transport": "http",
            "url": "https://api.smith.langchain.com/v1/managed-tools/servers/exa/mcp",
        },
    },
)
```

For this example, set `MDA_DEV_DOCS_TOKEN`.

A user-owned connection — `connections.get(slug, {"type": "user"})` — resolves
per caller (OAuth or opaque). Any runtime access interrupts the run when a grant
is missing, including access from a custom tool or middleware. Connector
declarations use the same behavior in a pre-run gate, so all known grants can be
requested before the first model call. This path works in `mda deploy` and
`mda dev` when `LANGSMITH_API_KEY` and `LANGSMITH_WORKSPACE_ID` are set and
the workspace connection rows exist. Reading a user-owned grant needs the
`workspaces:manage` permission for that `LANGSMITH_API_KEY`. Without it, the run
fails with `credential access requires workspaces:manage or permission on the
owning deployment` (status 403). Signed-in Studio users are identified by
their LangSmith `ls_user_id`. Local development uses separate user connections
from deployed Studio. OAuth completes on
LangSmith’s platform callback URL. The local UI must show
`credential_authorization_required` and resume after the user connects.

When a LangSmith-managed OAuth MCP server needs authorization, the runtime
reports its connection URL; complete OAuth there, then retry the run. API-key
MCP servers require the caller to connect the key in LangSmith Tools before
invoking one of their tools.

Deploy the project first, then provision its connections with
`mda connections create`. Agent-owned opaque secrets are scoped to that
deployment, so the command refuses to create one before `mda deploy`.
`mda deploy` fails when a slug a project declares is missing from the
workspace.

Use `include_tools` or `exclude_tools` inside a server config to select a
subset. Tool names are raw MCP tool names before the native `{server}_` prefix
is applied, so `"include_tools": ["search"]` on server `langchainDocs` exposes
`langchainDocs_search`.

## Connections in the sandbox proxy

Authenticate GitHub API requests and HTTPS git commands with the caller's
connection. Create the `github` workspace connection first.

```python
# sandbox/__init__.py
from managed_deepagents import define_sandbox, connections, bearer, basic

github = connections.get("github", {"type": "user"})

sandbox = define_sandbox(
    proxy_config={
        "rules": [
            {
                "name": "github-api",
                "match_hosts": ["api.github.com"],
                "headers": [{"name": "Authorization", "value": bearer(github)}],
            },
            {
                "name": "github-git",
                "match_hosts": ["github.com"],
                "headers": [
                    {"name": "Authorization", "value": basic("x-access-token", github)}
                ],
            },
        ],
        "access_control": {"allow_list": ["api.github.com", "github.com"]},
    }
)
```

| Header value | Sent value |
| --- | --- |
| `ref` | Raw token |
| `bearer(ref)` | `Bearer <token>` |
| `basic(username, ref)` | `Basic <base64(username:token)>` |

Use `{"type": "agent"}` for agent credentials. Omit header `type` for
connections; MDA sets `"opaque"`. Basic usernames must be non-empty and contain no `:`.

- **Refresh:** MDA resolves credentials before each command and updates changed
  values. A failed proxy update prevents the command from running.
- **Authorization:** Missing user grants interrupt before the first model call;
  revoked grants interrupt before the next command.

**Limits:**

- Long commands and background processes can fail if their tokens expire.
- Runs in one thread share a proxy config. Later runs replace the credentials
  used by background processes. Concurrent users can overwrite each other's credentials.

## CLI

Create a new project:

```bash
mda init my-agent
```

Build locally:

```bash
mda build ./my-agent
```

Run on the local LangGraph dev server:

```bash
mda dev ./my-agent
```

`mda dev` requires `uv` on `PATH`, but it resolves the local LangGraph dev
server automatically; you do not need to install a global `langgraph` command.

Deploy to LangSmith:

```bash
mda deploy ./my-agent
```

The generated build is written to `<root>/.mda/build` by default.

Select the deployment's Python version in `pyproject.toml`:

```toml
[project]
requires-python = ">=3.11"

[tool.mda]
python-version = "3.14"
```

Use a quoted `major.minor` from 3.11–3.14. MDA writes that version into the
generated `langgraph.json` and selects the matching Agent Server image.
`requires-python` remains the project's compatibility requirement.

`MDA_PYTHON_VERSION` overrides the project setting for one build. Without
either setting, MDA selects the newest supported version allowed by
`requires-python`, or 3.14 if no requirement is declared. MDA rejects
incompatible targets and warns when the image's exact patch version determines
compatibility. The dependency install checks the actual interpreter.

Set `python-version` explicitly to keep the same minor version when MDA adds
support for newer Python versions.

This selects the deployment image's Python version. Local build processing,
`mda dev`, Harbor task images, and execution sandboxes use separate interpreters.

Common deploy options:

```bash
mda deploy ./my-agent --name my-agent-dev --deployment-type dev
mda deploy ./my-agent --workspace-id "$LANGSMITH_WORKSPACE_ID"
mda deploy ./my-agent --no-wait
```

Read the deployed agent's server logs:

```bash
mda logs ./my-agent
mda logs ./my-agent --lines 200 --level error
mda logs ./my-agent > agent.log
```

In a terminal `mda logs` streams new output until you press Ctrl-C. When the
output is piped or redirected it prints the most recent lines (1000 by default)
and exits.

Tear it down again:

```bash
mda delete ./my-agent
```

`mda delete` (alias `mda destroy`) removes the LangSmith deployment, the tracing
project created alongside it, the deployment's Context Hub repo (plus any legacy
per-user or org child memory repos left from older runtimes), and the managed
sandboxes the deployment created. It asks for confirmation first; pass `--yes`
to skip the prompt in scripts. Agent memory and thread history are not
recoverable afterwards.

Sandboxes are matched by name: the runtime names each one
`{deployment}--{digest}` of the thread id, which also lets a restarted
deployment re-adopt its existing sandbox instead of stranding it. Recipe changes
(`setup.sh` or bake base) produce a new deploy-time snapshot; live threads keep
their boxes until reclaim. Sandboxes created before this behavior existed are
unnamed and are left to
LangSmith's idle-stop and retention window.

Before deploying, make sure your model provider key such as `OPENAI_API_KEY`
or `ANTHROPIC_API_KEY` is available in the project `.env` or LangSmith
workspace secrets; a value exported in your shell is not deployed. For
LangSmith itself, set `LANGSMITH_API_KEY` in `.env` or your shell, or run
interactively and press Enter at the prompt to sign in with your browser (the
CLI creates a key and writes it to `.env`). Use `LANGSMITH_WORKSPACE_ID` or
`--workspace-id` when your credentials require a workspace selection.
