# Open SWE Desktop

> [!IMPORTANT]
> This desktop client is experimental. The web UI is the recommended way to use Open SWE.

The Electron package ships the compiled Open SWE web UI. Users configure only the URL of a
compatible Open SWE backend; they do not need a separately hosted dashboard.

Desktop users can choose **This Mac** in the new-task composer to run the same Open SWE LangGraph agent over a selected local project. Electron owns a loopback-only LangGraph server, proxies it to the bundled UI, and stops it with the app. Local threads use the same streaming protocol, graph, tools, subagents, and middleware assembly as cloud threads; only the filesystem backend and unavailable cloud integrations differ.

The composer's workspace selector chooses where a local thread runs. **Current checkout** (the default) runs the agent in the project directory itself, on whichever branch the branch picker selects. **New worktree** gives the thread its own git worktree, checked out from the selected base branch on a placeholder `open-swe/local-<id>` branch that the agent renames after it reads the request — so your own checkout is never touched and up to ten local threads can run at once without contending for a working tree. Deleting a thread removes its worktree along with anything uncommitted in it. Only one agent may work in a given tree at a time, so starting a thread in a checkout another agent is running in, or switching that checkout's branch under it, is refused.

The packaged app bundles its Python runtime and locked Open SWE dependencies. Source development uses `uv run langgraph dev`. Provider credentials stay in the local LangGraph process and are not inherited by agent shell commands. Added projects and local thread history are persisted in the desktop app's local data.

For local OpenAI models, the app can use either `OPENAI_API_KEY` or a ChatGPT subscription. When no
API key is configured, sending the first local task opens the system browser for ChatGPT sign-in.
OAuth credentials are encrypted with the operating system's secure storage, refreshed by Electron,
and made available to the local model client through an authenticated loopback broker. Refresh
tokens are never placed in the local backend environment or inherited by agent shell commands.

Local model calls also honor `LANGSMITH_GATEWAY_*` configuration. On managed macOS installs, the
app reads `LC_GATEWAY_KEY` from `launchctl` when no explicit gateway key is configured and enables
gateway routing for the local backend. Gateway and provider credentials are not inherited by agent
shell commands.

The side panel's **Changes** tab diffs the project against a git snapshot taken when the session
started, so it shows what the agent changed and not the working tree's prior state. It also shows
the workspace's branch and discovers its pull request when the GitHub CLI is installed and authenticated.

## Local MCP servers

Desktop reads `~/.open-swe/mcp.json` again at the start of every local run. The file uses the
standard `mcpServers` map; stdio entries accept `command`, `args`, `cwd`, `env`, and either
`env_vars` or `env_passthrough`. HTTP entries accept `url` and `headers`, including localhost
URLs. Commands are executables plus argument arrays, not shell command strings. The login-shell
environment is resolved once when Electron starts; `${VAR}` and `${env:VAR}` substitute its values
without shell evaluation. Only configure commands and servers you trust: stdio servers inherit
that environment, including the user's local credentials.

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${HOME}/projects"]
    },
    "service": { "url": "http://localhost:9000/mcp" }
  }
}
```

The existing `getMcpServers`, `saveMcpServer`, and `deleteMcpServer` preload methods manage this
file through trusted-renderer IPC. Enable switches live separately in `userData/mcp-enabled.json`.
Invalid JSON is reported rather than overwritten. OAuth credentials never appear in these public
records or in `mcp.json`: encrypted blobs under `userData/mcp-credentials` use Electron safeStorage
with an OS-protected encryption key; Linux's insecure `basic_text` fallback is rejected.

HTTP servers with no Authorization header automatically use the installed MCP SDK's OAuth
provider when challenged. Set `auth_type: "none"` to opt out, or `auth_type: "oauth"` explicitly.
Discovery, dynamic client registration, PKCE authorization-code exchange, persisted expiry and
refresh are local. A loopback callback opens in the system browser through Electron. Public
pre-registered clients may specify `oauth_client_id`; confidential pre-registered client secrets
are not supported in the config file. A registered callback port must remain available; authorization
fails rather than changing an existing client's registered redirect. OAuth runs for the same local
server are serialized to avoid refresh-token rotation races.

### Agent integration contract

Import `agent.desktop_mcp` at desktop graph startup, **before constructing any shell backend**.
It consumes and removes `OPEN_SWE_MCP_BROKER_URL` / `OPEN_SWE_MCP_BROKER_TOKEN` from the process
environment so tools cannot inherit the broker capability. Electron supplies these variables through
the existing `BackendSupervisor.providerEnv` plumbing; do not accept them from run configuration,
thread metadata, model arguments, or MCP server JSON.

```python
from agent.desktop_mcp import local_mcp_tools

async with local_mcp_tools() as mcp_tools:
    await run_local_agent(extra_tools=mcp_tools)
```

`run_local_agent` above denotes the caller's existing agent assembly/invocation, not a new API.
The context must enclose the **entire run**, including streaming and subagent calls. It retains
stdio processes, MCP client sessions and HTTP transports in an `AsyncExitStack`; cancellation or
failure closes them. `await local_connections()` alternatively returns enabled transport records
for a trusted loader; those records can contain local headers/environment and a dashboard session
cookie and must never be exposed to the model, renderer, traces, or durable run metadata.

At each run the authenticated, loopback-only Electron broker supplies fresh local records and
trusted cloud runtime metadata (`backend_url`, `cookie_name`, `session_token`) from the selected
backend and Electron's existing session cookie jar. The loader lists `/dashboard/api/mcp-connections`
and connects only to `/dashboard/api/mcp-connections/{id}/proxy`, never the upstream URL or its
secrets. Local names override matching cloud names, including disabled local entries. Cloud list
or connection failures surface rather than silently omitting tools. With no cloud session, local
servers and local OAuth do not require a cloud backend.

The graph owner must wire the context above in `server.py`; this desktop module does not register
routes. The backend owner must expose the authenticated list and proxy routes. No new Python
local dashboard route or dependency is needed.

## How it connects

The bundled UI runs at an internal `open-swe://app` origin. Electron proxies its
`/dashboard/api/*` requests to the selected backend, so the browser never receives a LangSmith API
key and never calls the raw LangGraph API directly. GitHub login creates the same signed dashboard
session used by the web UI.

Packaged builds ask for the organization's backend URL on first launch and store it in the app's
local user data. They have no maintainer-hosted default. Use **Open SWE → Backend URL…** to switch
deployments; switching clears the previous deployment's local session data.

The backend's GitHub App must allow `<backend-url>/dashboard/api/auth/callback` as a callback URL.
Set `ALLOWED_GITHUB_ORGS` on the backend to prevent GitHub users outside the organization from
creating dashboard sessions.

The desktop sign-in screen also offers **Continue in local mode**. This skips GitHub sign-in and
limits the Agents workspace to projects and threads on **This Mac**; cloud threads, settings, and
other account-backed features remain behind sign-in. The choice is remembered on that computer,
and **Sign in for cloud mode** remains available from the local sidebar.

## Install on macOS

Install Git, Node.js 22, and `uv`, clone this repository, then run this from its root:

```bash
make install-desktop
```

The command fast-forwards to the latest `main`, builds Open SWE Desktop, and installs it in
`/Applications` (or `~/Applications` when needed). Run it again to update and replace the app; saved
backend settings, login sessions, and projects are preserved.

## Local development

Install the workspace dependencies, then run the backend, desktop app, and web UI independently:

```bash
pnpm install # from the repo root

# terminal 1
make dev

# terminal 2
make desktop

# terminal 3 (optional web UI)
make web
```

`pnpm run dev:desktop` is equivalent to `make desktop`. The desktop app starts its private local-agent backend on a random loopback port while connecting cloud features and GitHub login to the shared backend at `http://localhost:2024`.

Source launches use an isolated `Open SWE Development` Electron profile, so the dev app can run
beside an installed `Open SWE` app without sharing its login session, backend configuration,
projects, or single-instance lock. The dev window is labeled **Open SWE Development**; its first
launch may require signing in and adding projects again.

A separate agent installation is not required. Confirm `uv --version` succeeds before starting the desktop app in development.

Development defaults to `http://localhost:2024`. Point to another backend with:

```bash
pnpm --dir desktop run start -- --backend-url=https://open-swe-api.example.com
```

`OPEN_SWE_BACKEND_URL` provides the same override. Resolution order is command-line argument,
environment variable, saved first-launch configuration, then the local development default.
The original `--url` and `OPEN_SWE_DESKTOP_URL` names remain accepted for compatibility.

## Packaging

```bash
pnpm --dir desktop run pack # unpacked application for the current platform
pnpm --dir desktop run dist # installer for the current platform
```

Both commands build `ui/` and package its static output with Electron. Build outputs are written
to `desktop/dist/`.

## macOS releases

`desktop/package.json` is the latest stable version. Every **Promote main to prod** run publishes a
prerelease nightly from the promoted commit with a UTC timestamp, such as
`desktop-v0.2.3-nightly.20260902080000`. Nightly releases never publish the stable version.

Stable releases use a deliberate bump, test, release process: bump `desktop/package.json` in a normal
pull request, merge it to `main`, test the resulting code as needed, then run **Release Desktop**
manually. The workflow publishes the exact package version and fails if that stable release is
already complete. A partial stable release remains manually retryable.

Both paths compile the current `ui/` bundle, sign and notarize the Electron app, verify the resulting
app and DMG, create the tag, and publish the DMG, macOS zip, and app zip. Desktop-prefixed tags keep
this release stream separate from web and backend releases; the workflow packages the web UI but
does not deploy or otherwise change the hosted web app.

The workflow requires these GitHub Actions secrets:

- `APPLE_SIGNING_CERT`: base64-encoded Developer ID Application `.p12` certificate
- `APPLE_SIGNING_CERT_PASSWORD`: password for the certificate
- `APPLE_API_KEY`: App Store Connect `.p8` key contents
- `APPLE_API_KEY_ID`: App Store Connect key ID
- `APPLE_API_ISSUER`: App Store Connect issuer ID

Local packaging remains available without those credentials; signing and notarization are performed
by the release workflow.

## Deployment security

The backend URL is public configuration, not a credential. Dashboard routes require an
`osw_session` cookie issued after GitHub login, and `ALLOWED_GITHUB_ORGS` controls who may complete
that login. CORS alone is not access control.

Raw LangGraph routes are a separate boundary. A deployment using `LANGGRAPH_AUTH_TYPE=noop` must
keep those routes behind a private network, authenticated gateway, or custom LangGraph auth. An
external user does not need the deployment's server-side `LANGSMITH_API_KEY` to call an exposed,
unauthenticated LangGraph route.
