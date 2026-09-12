---
type: integration reference
title: Observability and Connected Tool Integrations
description: How LangSmith Gateway model routing, generic workspace and personal MCP connections, Notion OAuth, and sandbox browser tools are loaded and scoped. Covers encrypted credential handling, network boundaries, lazy tool loading, and failure behavior.
tags: [integrations, observability, mcp, credentials, authorization, langsmith]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-12T08:12:50.175Z
sources:
  - id: openwiki-source-f5844ea923486ce19e75076a
    resource: repo://agent/credential_scope.py
  - id: openwiki-source-b26707b64bee931c416620a7
    resource: repo://agent/dashboard/notion_oauth.py
  - id: openwiki-source-941341430e1d08d8e7e54dfe
    resource: repo://agent/dashboard/user_credentials.py
  - id: openwiki-source-c5408a622ba19f3bc7aea7a8
    resource: repo://agent/dashboard/user_mcps.py
  - id: openwiki-source-3329325fc0e8afac3eeea054
    resource: repo://agent/dashboard/workspace_mcps.py
  - id: openwiki-source-607d21f6c1c8daf2e2fbd444
    resource: repo://agent/mcp/models.py
  - id: openwiki-source-6506a11d150e73042a77db68
    resource: repo://agent/mcp/runtime.py
  - id: openwiki-source-e2bb7ecc1a77d417d7f47bba
    resource: repo://agent/mcp/transport.py
  - id: openwiki-source-9103280889fa6c4d9c5bb0df
    resource: repo://agent/middleware/dynamic_tools.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-2cd7e2018ae35c5972204803
    resource: repo://agent/tool_loaders/notion_mcp.py
  - id: openwiki-source-49907d748d9e1812d9705ce0
    resource: repo://agent/tool_loaders/stagehand_browser.py
  - id: openwiki-source-f0db445078d7a8158aa93724
    resource: repo://agent/utils/gateway.py
  - id: openwiki-source-56ade344fdbe7d47c84f008f
    resource: repo://agent/utils/model.py
  - id: openwiki-source-accd67905f62487c623d11f6
    resource: repo://tests/dashboard/test_user_mcps.py
  - id: openwiki-source-9767f97ec4ee247e8cb0373e
    resource: repo://tests/dashboard/test_workspace_mcps.py
  - id: openwiki-source-7b40efabe9016e7bf1bb2d30
    resource: repo://tests/tools/test_mcp_oauth.py
  - id: openwiki-source-ef912362699aed187e3ae082
    resource: repo://tests/tools/test_mcp_sources.py
  - id: openwiki-source-12c0d7edd7c7aa9c439b74d6
    resource: repo://tests/tools/test_mcp_transport.py
  - id: openwiki-source-4865a62f25f63e6c6db101d4
    resource: repo://tests/tools/test_notion_mcp_tools.py
  - id: openwiki-source-1207cab8934fb34eec15605a
    resource: repo://tests/tools/test_workspace_mcp_tools.py
generated: { by: "openwiki/0.4.2", at: "2026-09-12T08:12:50.175Z" }
---

# Observability and Connected Tool Integrations

This deployment's observability integration is the optional **LangSmith LLM Gateway**: it routes model-provider traffic through LangSmith rather than exposing a LangSmith trace-inspection tool surface. Connected tools are supplied through generic MCP connections and Notion's hosted MCP service. These are optional capabilities: a connection that is absent, not selected, inaccessible, or fails discovery is not added to the run.

See [Authentication and security](../concepts/auth-and-security.md) for the wider trust model, [Tools](../concepts/tools.md) for tool availability, [Agent graph](../architecture/agent-graph.md) for agent construction, and [Configuration](../operations/configuration.md) for deployment settings.

## Boundaries and scope

Provider calls made by generic MCP and Notion tools originate in the LangGraph server process. Connection headers, OAuth client secrets, and Notion access tokens are encrypted before Store persistence and are attached only to that server-to-provider request. They are not placed in the task sandbox. The Stagehand browser is the deliberate exception: its requests run in the thread's sandbox and can operate against sandbox-local services.

There are two generic MCP ownership scopes:

- **Workspace connections** live in the `workspace_mcps` Store namespace. Administrators manage them for the deployment.
- **Personal connections** live in `user_mcps/<normalized-login>`. They are available only to the saved owner of a private thread who starts the run.

A private thread must have a saved owner and the current triggering GitHub login must match it. Public threads have no personal credential login. If scope cannot be resolved, the agent omits personal MCP and Notion tools rather than guessing an owner. Workspace connections remain a separate, deployment-wide source.

```mermaid
flowchart TD
  Run["Agent run"] --> Scope["Resolve private credential owner"]
  Scope -->|"Private owner matches sender"| Personal["Personal MCPs and Notion"]
  Scope -->|"Public or unknown scope"| NoPersonal["No personal integrations"]
  Run --> Workspace["Workspace MCPs"]
  Personal --> Server["LangGraph server"]
  Workspace --> Server
  Server --> Remote["Hosted MCP provider"]
  Sandbox["Thread task sandbox"] --> Browser["Stagehand Chromium"]
```

The server owns credentialed MCP calls; private ownership gates personal sources, while Stagehand stays inside the sandbox.

## Generic MCP connections

### Configure, discover, then allowlist

An MCP connection has a stable name, an HTTPS URL, either `streamable_http` or `sse` transport, an enabled flag, optional authentication headers or OAuth client credentials, and an `allowed_tools` list. A newly saved connection exposes no tools until its owner discovers the remote catalog and saves selected tool names. Discovery is available for workspace administrators and for the signed-in owner of a personal connection; it returns names and descriptions and does not execute tools.

URLs reject embedded credentials, fragments, control whitespace, and query parameters that look like credentials. Header names and values are constrained, dangerous transport-controlled headers such as `Host` are blocked, and OAuth cannot coexist with an `Authorization` header. Public dashboard representations contain header names and OAuth metadata but exclude secret values. The settings APIs use redacted validation errors so malformed submissions cannot echo credentials.

For OAuth, the only supported grant is `client_credentials`, with `client_secret_post` or `client_secret_basic`. The client secret is encrypted. Reusing a stored secret is permitted only when the connection URL, token URL, and client ID still match; changing a destination requires a replacement secret. Runtime access tokens are cached by connection namespace, name, OAuth settings, and encrypted secret. This prevents identical personal configurations owned by different users from sharing tokens, refreshes before expiry, and retries a 401 once with a replacement token.

```mermaid
sequenceDiagram
  participant Admin as Connection owner
  participant Store as Encrypted Store
  participant Agent as LangGraph server
  participant Token as OAuth token endpoint
  participant Mcp as MCP server
  Admin->>Store: Save URL, allowlist, encrypted headers or secret
  Agent->>Store: Read enabled connection
  Agent->>Mcp: Discover selected catalog over pinned HTTPS
  Agent->>Token: Request client credentials token when configured
  Token-->>Agent: Bearer token
  Agent->>Mcp: Call allowlisted tool with headers or token
```

This flow shows the server-side credential path for a configured generic MCP connection; no secret value is exposed to the model or sandbox.

### Loading, precedence, and revocation

At agent construction, the server combines the workspace source first and the authorized personal source second. A personal connection with the same name replaces the workspace connection completely—even if it is disabled or selects no tools—so a lower-precedence workspace connection cannot leak through an explicit personal override. Connection catalogs are cached for 600 seconds and isolated by source namespace and connection revision.

Remote tool names are generated as bounded, namespaced names derived from the connection and remote tool name, with a hash suffix to prevent collisions. A wrapper checks the connection again immediately before every call: it rejects a missing or disabled connection, a changed URL/transport, a scope change, or a tool removed from the allowlist. Consequently, deletion, disabling, and allowlist changes revoke an already-loaded tool; changing its endpoint or ownership requires a new run.

Every discovery and invocation has a 30-second limit. Failure of one connection does not hide usable tools from another connection, while a failure to read a higher-precedence source returns no MCP tools instead of falling back to a workspace connection. Errors exposed to users are safe, operational hints; logs omit upstream credential-bearing details.

The HTTP client enforces the configured HTTPS origin, disables redirects and environment proxy settings, validates that DNS answers are public addresses, and pins a validated address while retaining the original Host header and TLS SNI hostname. This prevents an MCP server or DNS rebinding from redirecting credentials to a different or private origin.

### Dynamic loading

MCP definitions are installed through `DynamicToolMiddleware`. The model initially receives an inventory of integration tool names plus `load_integration_tools`, not all schemas. It must request loading before calling a connected tool. Loading is serialized per integration group and cached for the agent instance; after a successful load, the selected names are stored in run state and the tools are inserted for the next model call. An unknown, unavailable, or prematurely invoked integration tool produces an error message directing the agent to continue or load it first rather than crashing the run.

For executable non-local, non-summary runs, `get_agent` loads the workspace source and, when private scope is known, the personal source and Notion definitions concurrently. Local/desktop and summary-stop runs do not load them.

## Notion hosted MCP and OAuth

Notion tools use `https://mcp.notion.com/mcp` over streamable HTTP. A dashboard login dynamically discovers OAuth metadata and registers a client with the Notion MCP host, creates a PKCE verifier and state-bound short-lived flow, then exchanges the authorization code. OAuth discovery, authorization, registration, and token endpoints are all required to be HTTPS on `mcp.notion.com`. The saved user record encrypts access and refresh tokens and any client secret; status only reports connection and timestamp/expiry metadata.

`load_notion_tools(login)` is available only when `login` is the current private credential owner and has a usable token. It uses that token to discover tool definitions, then wraps each schema with a required `on_behalf_of` field. At invocation the wrapper resolves the participant and additionally requires that resolved login still be the private owner, fetches a fresh token, rebuilds the requested remote tool, and invokes it without `on_behalf_of`. A missing token asks the user to reconnect; a removed remote tool is rejected.

```mermaid
sequenceDiagram
  participant User as Private thread owner
  participant Dashboard as Dashboard OAuth flow
  participant Store as Encrypted user credentials
  participant Agent as LangGraph server
  participant Notion as Notion MCP
  User->>Dashboard: Authorize Notion access
  Dashboard->>Store: Save encrypted token record
  Agent->>Store: Read or refresh owner token
  Agent->>Notion: Discover tool definitions
  Agent->>Agent: Add on_behalf_of to each schema
  Agent->>Store: Re-read fresh owner token at invocation
  Agent->>Notion: Rebuild and invoke selected tool
```

This flow ensures discovery does not grant a different participant's Notion authorization. Token refresh is serialized per login; if a refresh returns `invalid_grant`, the dead connection is removed and reconnection is required. Credential lookup and refresh are intentionally fail-soft on the tool-loading path, so Notion failure removes optional tools rather than failing the run.

## LangSmith LLM Gateway

The LangSmith LLM Gateway is model routing, not a connected MCP tool. `make_model` centrally applies it to eligible models. The client authenticates to LangSmith with `LANGSMITH_GATEWAY_API_KEY` when present, otherwise `LANGSMITH_API_KEY`; LangSmith resolves the actual provider secret from workspace Provider Secrets and applies gateway policy and tracing.

A `gateway_enabled` team setting is authoritative when set. Otherwise, `LANGSMITH_GATEWAY_ENABLED` controls the deployment default; if that variable is unset, configuring a dedicated gateway key enables routing by default. `LANGSMITH_GATEWAY_BASE_URL` changes the gateway host. Only `openai`, `anthropic`, `baseten`, `fireworks`, and `google_genai` prefixes have gateway paths. Unsupported providers or absent LangSmith credentials log a warning and use direct provider routing rather than preventing model construction. OpenAI routing retains the Responses API unless `LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES=false` explicitly selects Chat Completions.

## Sandbox browser boundary

Stagehand exposes navigation, action, observation, extraction, and close operations only when `SANDBOX_TYPE` is `langsmith`, the configured model uses the `anthropic` or `openai` provider, and an appropriate model API key is available. The model defaults to `anthropic/claude-sonnet-4-5`. Each browser request is base64 JSON sent to a long-lived Stagehand runtime over a Unix socket inside the sandbox; the runtime is health-checked and started when absent. Execution or malformed-response failures become structured failure results.

## Focused verification

`tests/dashboard/test_workspace_mcps.py` verifies encrypted/redacted storage, validation, admin and same-origin routes, and selection workflows. `tests/dashboard/test_user_mcps.py` checks normalized user isolation and dashboard ownership. `tests/tools/test_workspace_mcp_tools.py`, `tests/tools/test_mcp_sources.py`, `tests/tools/test_mcp_oauth.py`, and `tests/tools/test_mcp_transport.py` cover namespacing, revocation, precedence, OAuth token behavior, and network-origin protections. `tests/tools/test_notion_mcp_tools.py` covers absent credentials, loader degradation, schema wrapping, and fresh-token invocation.
