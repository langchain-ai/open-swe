<!-- markdownlint-disable MD033 MD041 -->

<div align="center">
  <a href="https://docs.langchain.com/langsmith/python/managed-deep-agents-overview">
    <img alt="Managed Deep Agents logo" src="https://raw.githubusercontent.com/langchain-ai/managed-deepagents/main/logo.png" width="70%">
  </a>
</div>

<div align="center">
  <h3>The code-first, managed production path for Deep Agents.</h3>
</div>

> [!IMPORTANT]
> **Public beta.** Managed Deep Agents is in public beta. The API, workspace
> shape, and runtime contract may change. See the
> [docs](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview)
> for getting started and updates.

`managed-deepagents` helps you take a local Deep Agent from code to a managed
LangSmith service. You author agent behavior in TypeScript or Python, organize
the project in a predictable shape, and use the `mda` CLI to package and deploy
it to the managed runtime.

Managed Deep Agents includes:

- **Code-first authoring** with TypeScript and Python interfaces.
- **CLI deployment** through `mda build`, `mda deploy`, and `mda logs`.
- **Managed runtime wiring** for platform-owned backend, store, and
  checkpointing.
- **Production infrastructure** for durable execution, safe code execution, and
  LangSmith observability.

## Quick Start

Install the package for the language you use to author your agent. Both packages
include the `mda` CLI and the language-specific authoring interface:

- npm: TypeScript projects and `defineDeepAgent`
- PyPI: Python projects and `define_deep_agent`
- schedules: `defineSchedule` / `define_schedule`

```bash
npm install managed-deepagents
uv tool install managed-deepagents
```

> [!NOTE]
> **Private beta: dev releases only.** We currently publish only pre-release
> (dev) builds and no stable `latest` version yet. Install the dev channel
> explicitly:
>
> ```bash
> npm install managed-deepagents@dev
> uv tool install --prerelease allow managed-deepagents
> ```

Start a new project with `mda init`. In a terminal, the CLI asks you to name the
agent:

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

The install channel selects the project language: an npm-installed `mda`
scaffolds and compiles TypeScript, while a PyPI-installed `mda` scaffolds and
compiles Python. Each CLI package includes the matching managed runtime.

`mda init` takes the decisions you have already made, so the project arrives
shaped rather than needing to be edited into shape:

```bash
mda init my-agent \
  --instructions "You triage inbound support tickets." \
  --model anthropic:claude-sonnet-4-5 \
  --no-sandbox
```

`--instructions-file <path>` reads a longer prompt from a file, or from stdin
with `-`. Every new project includes an `identity.ts` / `identity.py` that
explicitly selects LangSmith API-key authentication. Callers send their
workspace key as `x-api-key`, while MDA retains its thread and store
authorization hooks. `--memory agent` writes a `memory.ts` / `memory.py` declaring
deployment-shared memory at `/memories/agent/`; durable memory is opt-in, so
without the flag the agent keeps nothing between runs. `--no-sandbox` leaves
out the managed sandbox. `-c slack` (also `--channel slack` or `--channels slack`)
writes `channels/slack.ts` / `channels/slack.py` so the next deploy can add the
agent to Slack; the same file can be added later with `mda channels init slack`.

Then build, run locally, or deploy from the project root:

```bash
mda build
mda dev
mda deploy
```

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

## Project Shape

A Managed Deep Agent project is a single Deep Agent organized into predictable
files and folders. The agent entry must live at the project root as `agent.ts`,
`agent.tsx`, or `agent.py`.

```text
my-agent/
  agent.ts | agent.py       # named `agent` export/variable
  identity.ts | identity.py # managed authentication (included by `mda init`)
  instructions.md           # managed system prompt
  package.json | pyproject.toml
  .env                      # local deploy secrets, never committed
  schedules/                # optional managed cron schedules
  tools/                    # optional custom tools and MCP declaration
    mcp.ts | mcp.py         # optional MCP server declaration
  middleware/               # optional middleware
  skills/                   # optional skills synced to Context Hub
  sandbox/                  # LangSmith sandbox (`mda init` includes this; delete to opt out)
```

The CLI copies your project files into the managed build and generates the small
entry module needed by the hosted runtime. Build output is written to
`<root>/.mda/build` and includes a generated `langgraph.json`.

The system prompt is managed: write it in `instructions.md` next to your agent
file and the CLI embeds it at deploy time. You don't set `systemPrompt` /
`system_prompt` yourself.

## TypeScript

Use `defineDeepAgent` to declare the agent you want the managed runtime to
compile:

```ts
import { defineDeepAgent } from "managed-deepagents";
import { queryDB } from "./tools/query-db";

// The system prompt comes from instructions.md next to this file.
export const agent = defineDeepAgent({
  name: "research-assistant",
  model: "openai:gpt-5.5",
  tools: [queryDB],
});
```

## Python

Use `define_deep_agent` when authoring in Python:

```python
from managed_deepagents import define_deep_agent

# The system prompt comes from instructions.md next to this file.
agent = define_deep_agent(
    name="research-assistant",
    model="openai:gpt-5.5",
    tools=[query_db],
)
```

In both languages, `name` is required: MDA uses it as the LangGraph assistant
id and the default LangSmith deployment name. The managed runtime owns
`backend`, `store`, and `checkpointer`, and embeds the system prompt from
`instructions.md`. Agent definitions should focus on behavior: tools,
middleware, model configuration, and skills.

### Python deployment version

Select the Agent Server Python version in `pyproject.toml`:

```toml
[project]
requires-python = ">=3.11"

[tool.mda]
python-version = "3.14"
```

MDA supports Python 3.11–3.14. `python-version` selects the deployment target;
`requires-python` declares project compatibility. MDA writes the target to
`python_version` and the image tag in the generated `langgraph.json`.

`MDA_PYTHON_VERSION` overrides the project setting for one build. Without
either setting, MDA selects the newest supported version allowed by
`requires-python`, or 3.14 if no requirement is declared. Incompatible targets
are errors. If compatibility depends on the image's exact patch version,
MDA warns; the dependency install checks the actual interpreter.

Set `python-version` explicitly to keep the same minor version when MDA adds
support for newer Python versions.

This setting controls the deployment image. Local build processing, `mda dev`,
Harbor task images, and execution sandboxes select their interpreters separately.

## Schedules

Add a file under `schedules/` that exports a named `schedule`. During deploy,
the CLI reconciles these declarations with LangSmith cron jobs targeting the
managed `agent` graph. Prompt schedules are sent as a normal user message, and
stateless runs default to `on_run_completed: "delete"` so temporary threads are
cleaned up after each run.

```ts
// schedules/daily-digest.ts
import { defineSchedule } from "managed-deepagents";
import prompt from "./daily-digest.md" with { type: "text" };

export const schedule = defineSchedule({
  cron: "0 8 * * 1-5",
  timezone: "America/Los_Angeles",
  prompt,
});
```

Schedule declarations must be statically serializable literals or top-level
constants. Prefer keeping longer cron prompts in a sibling `.md` file and
importing them with `with { type: "text" }` — `mda` inlines the file at compile
time, and the npm package ships ambient `*.md` typings so TypeScript accepts the
import. LangSmith cron schedules are UTC by default; use `timezone` when you
want LangGraph's cron scheduler to interpret the expression in an IANA timezone.

`mda dev` provisions the same schedules on the local server once it answers,
and again whenever an edit to `schedules/` or a `.env` restart changes them, so
they fire locally as they would after a deploy. Pass `--no-schedules` to keep
them off while you iterate. Python projects run them on the local in-memory
server; the JavaScript dev server that hosts TypeScript projects has no cron
support yet, so `mda dev` says that schedules are not active locally instead.

Use the runtime `schedules` API to create recurring schedules and schedules that
run once, for the current user or for the agent, while the agent runs. See the
[TypeScript](packages/npm/README.md#runtime-schedules) and
[Python](packages/pypi/README.md#runtime-schedules) READMEs.

## Sandbox

`mda init` scaffolds a LangSmith sandbox under `sandbox/`. MDA only enables the
sandbox when that declaration is present — delete the `sandbox/` directory to
opt out (for example for chat-only agents). MDA currently supports only
DeepAgents' `LangSmithSandbox`. For TypeScript, export a named `sandbox` from
`sandbox/index.ts`:

```ts
import { defineSandbox } from "managed-deepagents";

export const sandbox = defineSandbox({
  scope: "thread",
  idleTtlSeconds: 600,
});
```

For Python, define `sandbox` in `sandbox/__init__.py`:

```python
from managed_deepagents import define_sandbox

sandbox = define_sandbox(
    scope="thread",
    idle_ttl_seconds=600,
)
```

If `sandbox/setup.sh` exists, MDA embeds it and runs it once when the sandbox is
first provisioned. `scope: "thread"` creates one sandbox per thread and reuses
it across runs on that thread; `scope: "agent"` creates one process-level
sandbox shared by all threads handled by the agent. MDA owns sandbox naming,
reuse, and lifecycle. LangSmith's default sandbox template is used unless you
pass a `templateName`/`template_name` or `snapshotId`/`snapshot_id`.

Authored tools and middleware can use `runtime.backend` for sandbox file operations:

```ts
import type { ManagedDeepAgentRuntime } from "managed-deepagents";

async function writeReport(runtime: ManagedDeepAgentRuntime) {
  if (!runtime.backend) throw new Error("This tool requires a sandbox");
  const result = await runtime.backend.write("/workspace/report.txt", "Report ready");
  if (result.error) throw new Error(result.error);
}
```

The backend exposes `ls`, `read`, `write`, `edit`, `delete`, `grep`, and `glob`,
plus binary upload and download methods. Python also has async methods such as
`aread` and `awrite`. Methods use the standard Deep Agents arguments and results;
`delete` is optional and depends on the installed backend. The backend accesses
the current sandbox, with no Context Hub routes for skills or memory. Without a
sandbox, TypeScript sets `backend` to `undefined` and Python sets it to `None`.

## MCP Servers

Add `tools/mcp.ts` or `tools/mcp.py` to attach MCP servers. The file must expose a
named `mcp` declaration. By default, MDA exposes every tool loaded
from each declared server. Supply your own auth via static `headers` (or
equivalent) when the server requires credentials, or declare a managed
**connection** on the server config.

In TypeScript, MCP servers run on `@langchain/mcp-adapters` 2.x; see
[packages/npm/README.md#mcp-servers](packages/npm/README.md#mcp-servers) for the
0.8 migration notes.

The old `connectors/mcp.ts` and `connectors/mcp.py` file API remains available
with a warning during `0.7.x`. It will be removed in `0.8.0`.

TypeScript:

```ts
import { defineMcp, connections } from "managed-deepagents";

export const mcp = defineMcp({
  servers: {
    langchainDocs: {
      transport: "http",
      url: "https://docs.langchain.com/mcp",
      includeTools: ["search", "fetch"],
    },
    notion: {
      transport: "http",
      url: "https://mcp.notion.com/mcp",
      connection: connections.get("notion", { type: "user" }),
    },
  },
});
```

Python:

```python
from managed_deepagents import define_mcp, connections

mcp = define_mcp(
    servers={
        "langchainDocs": {
            "transport": "http",
            "url": "https://docs.langchain.com/mcp",
            "include_tools": ["search", "fetch"],
        },
        "notion": {
            "transport": "http",
            "url": "https://mcp.notion.com/mcp",
            "connection": connections.get("notion", {"type": "user"}),
        },
    },
)
```

In `mda dev`, an agent-owned connection reads from `MDA_DEV_<SLUG>` (slug
uppercased, hyphens to underscores). User-owned connections talk to Agent Auth
in every environment (including `mda dev`) after the runtime resolves the
caller’s Agent Auth principal; the pre-run gate collects missing OAuth grants
per person principal.

Use `includeTools` / `excludeTools` (Python: `include_tools` /
`exclude_tools`) inside a server config to select a subset. Tool names are raw
MCP tool names before the managed `{server}__` prefix is applied, so
`includeTools: ["search"]` on server `langchainDocs` exposes
`langchainDocs__search` when prefixing is enabled.

## Connections in the sandbox proxy

Use Agent Auth connections to authenticate sandbox HTTP requests without putting
credentials in commands or model messages.

See the GitHub API and HTTPS git examples for
[TypeScript](packages/npm/README.md#connections-in-the-sandbox-proxy) or
[Python](packages/pypi/README.md#connections-in-the-sandbox-proxy).

MDA resolves credentials before each command and updates changed values.
Missing or revoked user grants interrupt the run for authorization.

**Limits:**

- Long commands and background processes can fail if their tokens expire.
- Runs in one thread share a proxy config. Later runs replace the credentials
  used by background processes. Concurrent users can overwrite each other's credentials.

## CLI

Create a new project and move into it:

```bash
mda init my-agent
cd my-agent
```

Most commands below take the project path as an optional argument that defaults
to the current directory, so the examples run from the project root. Pass a path
explicitly — `mda deploy ./my-agent` — to work on a project without moving into
it first, which is what you want from a monorepo root or in CI. Channel
initialization always runs in the current project directory. `mda connections`
takes `--project <path>` instead, so the connection slug can be the argument.

Initialize a Slack channel:

```bash
mda channels init slack
```

Build a deployable managed app locally:

```bash
mda build
```

Run the compiled app on the local LangGraph dev server:

```bash
mda dev
```

Chat with it from the terminal:

```bash
mda chat
```

No dev server needed: chat attaches to one if it is already running, and
otherwise starts its own for the session. A server chat starts logs to
`.mda/dev-server.log` and does not reload on edits — run `mda dev` for that.

Replies stream as they are generated, tool calls are shown as they happen, and
a tool guarded by `interrupt_on` pauses for the approval choices its policy
allows. With per-call human-in-the-loop (`interrupt_mode="per_call"`), each tool
call is reviewed on its own, and chat can also edit its arguments or answer in
its place. Batched reviews that require editing or a custom response must be
completed in Studio. `/new` starts a fresh thread and `/exit` leaves. The closing line prints
a command with the project, assistant, and `--thread` options needed to resume
the same conversation later. Pending reviews are shown before the next message
when a thread is reopened.

Deploy it to LangSmith:

```bash
mda deploy
```

Common deploy options:

```bash
mda deploy --name my-agent-dev --deployment-type dev
mda deploy --workspace-id "$LANGSMITH_WORKSPACE_ID"
mda deploy --no-wait
```

Manage workspace connections the agent references with `connections.get(...)`:

```bash
mda connections create my-openai-key --secret-from-env OPENAI_API_KEY
mda connections list
mda connections get my-openai-key
mda connections delete my-openai-key
```

Connections are workspace-scoped (one slug per workspace). Agent-owned opaque
secrets are owned by the project's default-name MDA deployment, so
`mda connections create` requires that deployment to exist (`mda deploy`
first). `mda connections list` shows every row in the workspace, not just the
ones an agent uses. `mda deploy` fails when a connection this project declares
is missing from the workspace.
Create them with value flags (`--secret-from-env`, `--secret-from-file`,
stdin, or a prompt). User-owned connections are OAuth slots for now — the CLI
does not yet support creating user-owned opaque slots. Provision OAuth with
`--oauth` (catalog or manual BYOT via `--authorize-url` and `--token-url`);
optional flags include `--client-id` and `--scope` (repeatable). Without
`--authorize`, the pre-run gate collects a missing OAuth grant from each user at
runtime. Add `--authorize` to create an agent-owned grant and print its
authorization URL during project setup. The project must already have a
deployment with its authored agent name.

Several deployments can reference one slug, because `mda deploy` only checks
that the slug exists. Credentials on a connection have owners, though. An
agent-owned credential, an opaque secret or an `--authorize` grant, belongs to
the deployment that created it, so another deployment that references the slug
has no agent credential of its own. `mda connections create` fails when the slug
already exists, so it cannot add one. Give each deployment its own slug for
agent-owned connections, for example `support-bot-openai-key`.
`mda connections delete <slug>` removes the connection for every deployment
that references it. Slugs use lowercase letters and digits separated by single
hyphens or slashes, at most 64 characters.

Reading a user-owned connection needs the `workspaces:manage` permission for the
`LANGSMITH_API_KEY` the agent runs with. In `mda dev` that is your own key.
Without the permission, the run fails with `credential access requires
workspaces:manage or permission on the owning deployment` (status 403).

You name where the value already lives and `mda` reads it:

```bash
mda connections create my-openai-key --secret-from-env OPENAI_API_KEY
mda connections create my-openai-key --secret-from-file ./key.txt
mda connections create my-openai-key < ./key.txt
echo 'sk-...' | mda connections create my-openai-key
mda connections create my-openai-key
```

`--secret-from-env` reads the named variable from your shell or the project
`.env`. `--secret-from-file` stores the file's entire contents, so point it at a file holding
only the secret; aiming it at a `.env` or multiline file is refused. With no
value flag, a TTY prompts for the secret; a pipe or redirect reads stdin
(`echo 'sk-...' | mda connections create …` or `mda connections create … < key.txt`).

With only a slug, `mda connections create` first looks for a user-owned MCP
server in the project that uses that slug. If one matches, it creates an OAuth
connection for that server. Otherwise it treats the slug as an agent-owned
secret and asks for the value, so a mistyped MCP slug ends at the secret prompt:

```bash
mda connections create notion   # a user-owned MCP server uses "notion": OAuth
mda connections create notoin   # no match: prompts for a secret value
```

A connection is one named entry in the workspace vault, holding either an opaque
secret like an API key or an OAuth grant. For the OAuth kind, name the service
instead of typing its endpoints:

```bash
mda connections create notion-docs \
  --oauth notion \
  --client-id "$NOTION_CLIENT_ID" \
  --secret-from-env NOTION_CLIENT_SECRET
```

For a service identity that all runs of the deployed agent use, authorize the
agent when you create the connection:

```bash
mda connections create github --oauth github --authorize
mda connections create my-notion --mcp https://mcp.notion.com/mcp --authorize
mda connections create my-notion --authorize # Infer the MCP URL from the project.
```

Open the URL that the command prints and approve access at the provider.
`--authorize` is valid only for OAuth connections. It adds an agent-owned grant
to the connection it creates, independent of the connector declaration. It
cannot add a grant to a connection that already exists. To use
that grant at runtime, the connector must select `owner: "agent"`
(`owner="agent"` in Python). A connector that selects `owner: "user"` continues
to request a grant from each user at runtime.

Register the app at the provider with redirect URI
`https://api.smith.langchain.com/v1/agent-auth/oauth/callback`. The CLI prints it
on a successful create, and names it if `--client-id` is missing.

`--oauth <service>` reads the authorization endpoint, token endpoint, token
endpoint auth method, default scopes, and any authorization parameters the
service needs out of a catalog compiled into the CLI, so the only OAuth details
you supply are the client credentials for your own app.

A client id is public by spec and travels in the authorize URL, so it is passed
as a value. The client secret uses the same `--secret-from-env` / `--secret-from-file` / stdin
/ prompt sources as any other connection value, so it never lands in shell
history.

Override any part of what the catalog supplies:

```bash
mda connections create gtm-readonly \
  --oauth google \
  --client-id "$GOOGLE_CLIENT_ID" \
  --secret-from-env GOOGLE_CLIENT_SECRET \
  --scope https://www.googleapis.com/auth/drive.readonly \
  --allowed-scope https://www.googleapis.com/auth/drive.readonly \
  --allowed-scope https://www.googleapis.com/auth/drive \
  --authorization-param prompt=select_account
```

`--scope` replaces the catalog defaults rather than adding to them.
`--allowed-scope` sets the ceiling any later authorization may request. It must
cover every `--scope`, and defaults to the `--scope` values.
`--authorization-param` merges over the catalog's parameters. `--auth-method`
picks how the client authenticates to the token endpoint
(`client_secret_basic`, `client_secret_post`, or `none` for a public client with
no secret), defaulting to the catalog service's method, or to
`client_secret_basic` for a provider the catalog does not cover.

See what the catalog covers, and where each service's apps are registered:

```bash
mda connections catalog
mda connections catalog --json
```

This reads the compiled-in table, so it needs no workspace, no API key, and no
network. For a service the catalog does not cover, pass `--authorize-url` and
`--token-url`.

Read the deployed agent's server logs:

```bash
mda logs
mda logs --lines 200 --level error
mda logs > agent.log
```

In a terminal `mda logs` streams new output until you press Ctrl-C. When the
output is piped or redirected it prints the most recent lines (1000 by default)
and exits.

Tear it down again:

```bash
mda delete
```

`mda delete` (alias `mda destroy`) removes the LangSmith deployment, the tracing
project created alongside it, the deployment's Context Hub repo (plus any legacy
per-user or org child memory repos left from older runtimes), the managed
sandboxes the deployment created, and the Agent Auth connections its channels
set up. It asks for confirmation first; pass `--yes`
to skip the prompt in scripts. Agent memory and thread history are not
recoverable afterwards.

Sandboxes are matched by name. `mda deploy` passes the deployment name to the
runtime, which names every sandbox it creates `{deployment}--{digest}`, so
teardown can tell a deployment's sandboxes apart from the rest of the
workspace's. The digest covers the sandbox scope plus how the sandbox was
provisioned, so editing `setup.sh` or switching snapshots gives the next run a
fresh sandbox rather than an old one built from the previous recipe. Sandboxes
created by agents deployed before this behavior existed are unnamed and are left
to LangSmith, which stops an idle sandbox and deletes it after its retention
window (see [Sandboxes](https://docs.langchain.com/langsmith/sandboxes)).

## Deploy Prerequisites

Before running `mda deploy`, make sure you have:

- Private beta access to Managed Deep Agents and LangSmith deployment
  permissions.
- A root-level `agent.ts`, `agent.tsx`, or `agent.py`.
- A `package.json` for TypeScript projects or `pyproject.toml` for Python
  projects.
- The package installed from the dev channel: `managed-deepagents@dev` or
  `uv tool install --prerelease allow managed-deepagents`.
- `npm` on `PATH` for TypeScript deploys, because the CLI generates a lockfile
  for the remote build. Other lockfiles (`pnpm-lock.yaml`, `yarn.lock`,
  `bun.lock`) are left out of the build.
- `LANGSMITH_API_KEY` in your environment or project `.env`. If neither is set,
  `mda deploy` prompts for a key or let you press Enter to sign
  in with the browser; browser sign-in mints a 2-hour personal access token
  cached on the machine (not written to `.env`). Paste a longer-lived key from
  Settings → API Keys when you want a durable project credential. See
  [`crates/mda/README.md`](crates/mda/README.md#langsmith-authentication).
- A model provider key such as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.
  For example, `model="openai:gpt-5.5"` uses `OPENAI_API_KEY` from the project
  `.env`. Setting `model="langsmith:openai/gpt-5.5"` sends model calls through
  LangSmith Gateway, which uses `OPENAI_API_KEY` stored in your LangSmith
  workspace secrets instead. Your LangSmith API key authenticates with Gateway;
  the workspace OpenAI key authenticates with OpenAI.
- `LANGSMITH_WORKSPACE_ID` or `--workspace-id` when your LangSmith API key
  requires a workspace selection.
- Optional `LANGSMITH_ENDPOINT` when you are not on prod US — set it to the
  platform API for that environment (for example
  `https://eu.api.smith.langchain.com`, `https://beta.api.smith.langchain.com`,
  or `https://dev.api.smith.langchain.com`). The CLI derives the matching host
  backend and uses the same origin for browser sign-in. Local LangSmith:
  `LANGSMITH_ENDPOINT=http://localhost:1980`. See `CONTRIBUTING.md` for the
  full environment table.

`mda deploy` loads `<project>/.env`, uses reserved LangSmith variables for the
deploy request, and forwards other variables as deployment secrets. Do not commit
real `.env` files.

## Package Documentation

For language-specific package details, see:

- [`packages/npm`](packages/npm/README.md) for TypeScript and Node.js projects.
- [`packages/pypi`](packages/pypi/README.md) for Python projects.
