---
type: operations reference
title: Configuration & Environment Variables
description: Central reference for Open SWE's runtime configuration and environment variables across sandbox provisioning, model selection, auth/webhooks, and third-party integrations, plus the langgraph.json runtime config and admin runtime overrides.
tags: [configuration, environment-variables, sandbox, models, auth, webhooks, integrations, operations]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-27T06:27:22.313Z
sources:
  - id: openwiki-source-068d65a84c760eb8d555055e
    resource: repo://agent/completion.py
  - id: openwiki-source-ef92164b6963a5a6100712cb
    resource: repo://agent/dashboard/admin.py
  - id: openwiki-source-b26707b64bee931c416620a7
    resource: repo://agent/dashboard/notion_oauth.py
  - id: openwiki-source-07762d55411a883aaa28e2ed
    resource: repo://agent/dashboard/sandbox_settings.py
  - id: openwiki-source-941341430e1d08d8e7e54dfe
    resource: repo://agent/dashboard/user_credentials.py
  - id: openwiki-source-e01f650ad19daacbf8aa5146
    resource: repo://agent/integrations/corridor_mcp.py
  - id: openwiki-source-654935a74cea8df94781a2a3
    resource: repo://agent/integrations/currents_tools.py
  - id: openwiki-source-91fc7c96eeba465eb9307d1c
    resource: repo://agent/integrations/datadog_mcp.py
  - id: openwiki-source-0b53777f0ea426a90cf976b4
    resource: repo://agent/middleware/model_call_timeout.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-ecd2116a1064fa0da51e5630
    resource: repo://agent/runtime/constants.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-56ade344fdbe7d47c84f008f
    resource: repo://agent/utils/model.py
  - id: openwiki-source-9393f5c0c83356ac7031b652
    resource: repo://agent/utils/sandbox.py
  - id: openwiki-source-8010c6e64af5a375d8d3b70b
    resource: repo://docs/CUSTOMIZATION.md
  - id: openwiki-source-bb241754e70259fd67d23952
    resource: repo://docs/INSTALLATION.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
generated: { by: "openwiki/0.4.2", at: "2026-08-27T06:27:22.313Z" }
---

# Configuration & Environment Variables

Open SWE is configured almost entirely through environment variables read at
process start plus a small set of runtime overrides an admin can change without a
redeploy. This page aggregates the variables that materially change how the
system boots, selects models, authenticates callers, and talks to integrations.
It complements the operational setup narrative in
[operations/deployment](deployment.md), the provider-specific detail in
[integrations/sandbox-providers](../integrations/sandbox-providers.md), and the
trust model in [concepts/auth-and-security](../concepts/auth-and-security.md).

Two rules cut across everything below:

- **Never store GitHub access tokens as deployment environment variables.** Git
  and `gh` inside a sandbox authenticate through the LangSmith sandbox proxy
  using tokens minted at runtime from GitHub App installation credentials.
- **Several security-relevant settings fail closed** when unset (the run-complete
  webhook secret, admin OIDC, org membership checks), meaning "unset" disables a
  capability rather than silently opening it.

## `langgraph.json` runtime config

`langgraph.json` declares how the LangGraph platform loads and runs the
application. It registers five graphs (`agent`, `reviewer`, `analyzer`, `chat`,
`scheduler`) by import path, mounts the FastAPI app that owns the webhooks and
dashboard API as the HTTP app (`agent.webapp:app`), and pins the Python and API
versions.

Checkpointer thread state is garbage-collected by a TTL policy: the `delete`
strategy sweeps every 60 minutes and expires checkpoints after a default TTL of
43200 minutes (30 days). The `env` key points the platform at the `.env` file
that supplies the variables described on this page.

## Sandbox

### Provider selection

`SANDBOX_TYPE` selects the sandbox backend and defaults to `langsmith`.
`create_sandbox` resolves the value against a registry (`SANDBOX_FACTORIES`)
mapping each provider name to an integration module and factory function; an
unknown value raises `ValueError` listing the supported types. Supported values
are `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local`. `local` runs
commands directly on the host with no isolation and is intended only for
development.

`validate_sandbox_startup_config` runs from the FastAPI lifespan hook so an
invalid provider configuration fails at boot rather than on the first sandbox
creation; for `langsmith` it delegates to `LangSmithProvider.validate_startup_config`.

### Base snapshot and resources (LangSmith)

For `SANDBOX_TYPE=langsmith`, new sandboxes boot from a base snapshot. The
deployment default is `DEFAULT_SANDBOX_SNAPSHOT_ID`, and resource shape is tuned
with optional variables (each documented with its default):
`DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES` (128 GiB),
`DEFAULT_SANDBOX_VCPUS` (4), `DEFAULT_SANDBOX_MEM_BYTES` (16 GiB),
`DEFAULT_SANDBOX_IDLE_TTL_SECONDS` (7200; 0 disables), and
`DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS` (2592000; 0 disables). Only the
`langsmith` provider honors `snapshot_id`/resource overrides in `create_sandbox`;
other providers ignore them.

`ENVIRONMENT_SNAPSHOT_PREFIX` overrides the `openswe` prefix used to name
environment snapshot captures, needed when several deployments share one
LangSmith workspace.

Sandboxes default to the same LangSmith credentials as tracing;
`SANDBOX_LANGSMITH_API_KEY` (falling back to `LANGSMITH_API_KEY` /
`LANGSMITH_API_KEY_PROD`) and `SANDBOX_LANGSMITH_ENDPOINT` (falling back to
`LANGSMITH_ENDPOINT`) point sandbox create/connect/delete, the GitHub proxy
config, and snapshot captures at a different workspace.

### Admin runtime override vs deployment default

`DEFAULT_SANDBOX_SNAPSHOT_ID` is only the *deployment default*. An admin can
override the base snapshot at runtime from the dashboard **Sandbox** page or via
`PUT /dashboard/api/sandbox-settings`, so a rebuilt base image rolls out without a
redeploy. The stored admin value wins; clearing it falls back to the env var.
`resolve_base_snapshot_id` encodes this precedence — admin setting, else env — and
`get_sandbox_settings` reports the effective value plus its `base_snapshot_source`
(`admin`, `env`, or `unset`). An environment with a ready snapshot still takes
precedence over this base.

The store lookup is deliberately fail-soft: `get_admin_base_snapshot_id` catches
store errors and returns `None` so a lookup failure during sandbox creation falls
back to `DEFAULT_SANDBOX_SNAPSHOT_ID` rather than failing the run. The stored
value is opaque, provider-scoped free text validated only for length
(`BASE_SNAPSHOT_MAX_CHARS`, 512 characters).

## Models

### Model selection and fallback

The primary model id is read from `LLM_MODEL_ID`, defaulting to `DEFAULT_MODEL_ID`
when unset. `LLM_FALLBACK_MODEL_ID` sets an explicit fallback; when unset a
provider-appropriate default (`fallback_model_id_for`) is used, and a fallback is
wired via `ModelFallbackMiddleware` only when it differs from the primary model.
Both are `provider:model` strings (e.g. `anthropic:claude-sonnet-5`,
`openai:gpt-5.6-sol`).

The default output budget is `DEFAULT_LLM_MAX_TOKENS` (64000) — a completion/
output budget, not the context window.

### Timeouts and recursion limits

Model calls are bounded at two layers:

- A **provider-level per-request timeout** defaulting to
  `DEFAULT_REQUEST_TIMEOUT_SECONDS` (600s), applied to OpenAI, Anthropic,
  Baseten, Google Gemini, and Fireworks, with `DEFAULT_MAX_RETRIES` (6) retries so
  a stall becomes a retry rather than a dead run.
- A **wall-clock deadline** around every model call via
  `ModelCallTimeoutMiddleware`, defaulting to
  `DEFAULT_MODEL_CALL_TIMEOUT_SECONDS` (900s) and overridable with
  `OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS`. It sits above the provider timeout and
  turns a silent transport hang (which is not an error the fallback middleware
  could otherwise react to) into a `ModelCallTimeoutError`.

Graph runs set `recursion_limit` to `DEFAULT_RECURSION_LIMIT` (9999), and the
agent/reviewer additionally cap model calls per run at
`MODEL_CALL_RECURSION_LIMIT` (5000) via `ModelCallLimitMiddleware`.

### LangSmith LLM Gateway routing

Model calls can be proxied through the LangSmith LLM Gateway.
`LANGSMITH_GATEWAY_ENABLED` (default `false`) is the deployment-level default;
`LANGSMITH_GATEWAY_API_KEY` supplies a dedicated key (falling back to
`LANGSMITH_API_KEY_PROD`, then `LANGSMITH_API_KEY`);
`LANGSMITH_GATEWAY_BASE_URL` overrides the gateway host; and
`LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES` (default `true`) controls Responses vs
Chat Completions for OpenAI. A per-workspace admin toggle stored in team settings
overrides the env default when set (an unset team value inherits it).

## Auth and webhooks

### Run-complete webhook secret (fail-closed)

`RUN_COMPLETE_WEBHOOK_SECRET` is a shared bearer token proving a
`/webhooks/run-complete` call came from Open SWE's own dispatch. `verify_run_complete_token`
fails closed: with no secret configured it rejects every call, so completion and
failure replies stay off until the secret is set. The module logs a startup
warning when the secret is unset.

### GitHub App and dashboard OAuth

The GitHub App identity is configured with `GITHUB_APP_ID`,
`GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID`, with inbound webhooks
verified by `GITHUB_WEBHOOK_SECRET`. Dashboard login uses a separate direct
GitHub OAuth flow (`GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET`), distinct
from the agent-runtime OAuth brokered by LangSmith (`GITHUB_OAUTH_PROVIDER_ID`,
with `X_SERVICE_AUTH_JWT_SECRET` minting the service JWTs that resolve a specific
user's GitHub token). Dashboard sessions and OAuth state are signed with
`DASHBOARD_JWT_SECRET`, and stored GitHub tokens are encrypted with
`TOKEN_ENCRYPTION_KEY`, which supports an ordered, most-recent-first key list for
rotation.

### Org allowlist and admin gates

`ALLOWED_GITHUB_ORGS` (and `ALLOWED_GITHUB_REPOS`) filter GitHub/Linear webhooks
(a repo is accepted if its org is allowlisted *or* its `owner/repo` is), gate
dashboard login, and add a prompt-level edit guard for Slack/dashboard requests;
membership checks fail closed on any GitHub API error. When both are empty all
repos are allowed.

`CONFIGURED_ADMINS` is a comma-separated GitHub login/email allowlist for admin
dashboard endpoints — empty means nobody is an admin. Admin-gated API requests
also accept GitHub Actions OIDC, allowlisted by `ADMIN_OIDC_SUBJECTS`
(the on/off switch — empty disables OIDC) with an optional `ADMIN_OIDC_AUDIENCE`
defaulting to `open-swe`.

`OBSERVABILITY_AUTHORIZED_EMAILS` is a comma-separated email allowlist for the
read-only team observability tools; `is_observability_authorized` grants access
to configured admins unconditionally and otherwise to emails in this list. Active
members of orgs in `ALLOWED_GITHUB_ORGS` also gain observability access when team
LangSmith credentials are connected.

## Integrations

Integration credentials are read in the LangGraph server process and attached to
MCP/API connections there; the sandbox never holds these credentials.

- **Corridor** MCP is enabled when a token is present in one of
  `CORRIDOR_API_TOKEN`, `CORRIDOR_MCP_TOKEN`, or `CORRIDOR_TOKEN` (or embedded in a
  URL query). The URL comes from `CORRIDOR_MCP_URL` / `CORRIDOR_MCP_SERVER_URL`,
  defaulting to `https://app.corridor.dev/api/mcp`; a non-Corridor host is rejected
  with a logged warning, and only the `analyzePlan` tool is exposed.
- **Datadog** credentials live in encrypted team settings and are attached as
  `DD_API_KEY`/`DD_APPLICATION_KEY` headers; `DATADOG_MCP_TOOLSETS` overrides the
  default `core` toolset.
- **Notion** MCP uses per-user OAuth against `https://mcp.notion.com/mcp`;
  `NOTION_MCP_CLIENT_NAME` (default `Open SWE`) names the registered OAuth client.
- **Currents** uses a per-user API key against `https://api.currents.dev/v1`.

## See also

- [operations/deployment](deployment.md) — end-to-end setup and rollout.
- [integrations/sandbox-providers](../integrations/sandbox-providers.md) —
  provider-specific sandbox configuration.
- [concepts/auth-and-security](../concepts/auth-and-security.md) — trust
  boundaries, token handling, and fail-closed behavior.
