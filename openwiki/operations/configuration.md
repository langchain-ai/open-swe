---
type: operations reference
title: Configuration and Environment
description: Explains Open SWE's lazy environment registry, persisted team and workspace settings, model and sandbox selection, secrets, and the boot-time validation that determines whether a server starts.
tags: [configuration, operations, environment-variables, startup-validation, sandbox, models, security, workspace-settings]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-18T12:31:08.965Z
sources:
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-913527bc7b548b4bf81f6a35
    resource: repo://openswe/completion.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-775d5704fff1c9b4f3e91941
    resource: repo://openswe/dashboard/workspace_settings.py
  - id: openwiki-source-1685d34aae8025be9332f45a
    resource: repo://openswe/dispatch.py
  - id: openwiki-source-b11ec0af4e40439361058935
    resource: repo://openswe/encryption.py
  - id: openwiki-source-1b32e9f41fa7e64702b380f6
    resource: repo://openswe/sandboxes/lifecycle.py
  - id: openwiki-source-d16a45e9fc6aa80a3708c88c
    resource: repo://openswe/sandboxes/providers/langsmith.py
  - id: openwiki-source-a4c632cb1c0a9a7a637ab9fe
    resource: repo://openswe/sandboxes/providers/registry.py
  - id: openwiki-source-919e16feae379651f2cbc1c9
    resource: repo://openswe/server.py
  - id: openwiki-source-cbab46b11893a9efc599e687
    resource: repo://openswe/utils/gateway.py
  - id: openwiki-source-4cc74089c0207ec1e5a6ca3b
    resource: repo://openswe/utils/model.py
generated: { by: "openwiki/0.4.2", at: "2026-10-18T12:31:08.965Z" }
---

# Configuration and Environment

Open SWE has two configuration planes:

- **Deployment configuration** is declared centrally in `openswe/config.py` and normally supplied through environment variables (including the `.env` file named by `langgraph.json`). It covers connectivity, credentials, provider selection, UI URLs, and deployment defaults.
- **Persisted settings** are instance-wide (team settings) or per-workspace records in LangGraph Store, and change selected behavior without a redeploy: team-wide review toggles and model defaults, workspace-level base snapshot and custom resource sizing. These are not substitutes for secrets or provider credentials, which remain deployment configuration.

This page describes configuration ownership, validation behavior, and important patterns rather than cataloging every variable. `openswe/config.py` is the complete registry; [deployment](deployment.md) covers installation. See [models, profiles, and instructions](../concepts/models-profiles-instructions.md) for model selection semantics.

## Environment variables and the ENV registry

The `ENV` registry centralizes environment variable declarations and reads. Code accesses variables through `ENV.<name>` or `ENV["name"]` rather than calling `os.environ` directly, so lazy reads capture late secret hydration, key rotation, and test mocking. Whitespace-only and empty values count as unset. The registry documents descriptions, defaults, secret status, aliases, and deprecated names; its accessors (`get`, `require`, `get_int`, `get_bool`, `get_list`) parse values typefully.

The canonical name wins over aliases. Deprecated names and aliases are registered once and centralized so `deprecated_in_use()` can report them, and suppresses warnings when both old and new names are set (e.g., LangGraph Platform injecting legacy `LANGCHAIN_*` alongside `LANGSMITH_*`). An undeclared variable name raises `AttributeError` or `KeyError`, making environment variables an explicit schema.

### Deployment topology

`langgraph.json` registers the agent, reviewer, review-scout, chat, and scheduler graphs and mounts `openswe.webapp:app` (FastAPI) as the HTTP application. The checkpointer uses a delete-based TTL strategy with a 60-minute sweep interval and 43200-minute default TTL (30 days). The file declares `.env` as the environment file.

## Startup lifecycle and validation

The FastAPI entrypoint is `openswe.api.app:create_app`. It pins the process to a single event loop before background workers are constructed, and pins again in the lifespan startup phase. The lifespan then runs four mandatory checks in order, aborting startup if any fail. After validation, it initializes background workers and services before yielding to serve routes. On shutdown it closes the database and stops workers.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
    Init["Import application"] --> Pin["Pin one event loop"]
    Pin --> Build["Create FastAPI application"]
    Build --> ValidateOrigins{"DASHBOARD_ALLOWED_ORIGINS<br/>contains '*'?"}
    ValidateOrigins -->|yes| Abort["Abort: credentialed CORS<br/>cannot use wildcard"]
    ValidateOrigins -->|no| Start["Lifespan startup"]
    Start --> CheckAuth["Validate GitHub login allowlist"]
    CheckAuth -->|fail| Stop["Raise and abort startup"]
    CheckAuth -->|pass| CheckSandbox["Validate sandbox configuration"]
    CheckSandbox -->|fail| Stop
    CheckSandbox -->|pass| CheckModel["Validate localhost model credentials"]
    CheckModel -->|fail| Stop
    CheckModel -->|pass| Workers["Initialize background workers"]
    Workers --> Serve["Serve routes and runs"]
    Serve --> Close["Close database and stop workers"]
```

### Validation details

**GitHub login gate**: Startup fails when neither `OPEN_SWE_LOCAL_AUTH_TOKEN` is set nor either `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` is non-empty. The deployment must gate dashboard access.

**Sandbox configuration**: Validation is provider-specific. For `SANDBOX_TYPE=langsmith`, it checks numeric resource fields (`DEFAULT_SANDBOX_*_BYTES`, `DEFAULT_SANDBOX_*_SECONDS`), numeric TTL fields (rejecting negative values for idle or delete-after-stop), and `SANDBOX_CREATE_EXTRA_JSON` (rejecting non-JSON or non-object). An unsupported provider name is rejected only when `create_sandbox` is called, not at startup.

**Model credentials** (localhost only): Validation runs only when `DASHBOARD_BASE_URL` is explicitly set to an `http://localhost` address. It checks the credential required by the deployment's `LLM_MODEL_ID` (or the fallback default): `OPENAI_API_KEY` for OpenAI, `ANTHROPIC_API_KEY` for Anthropic, and so on. Desktop OpenAI OAuth is accepted as a substitute for `OPENAI_API_KEY`. Runtime model choices (team settings, profile, thread) are not validated at boot.

**CORS wildcards**: If `DASHBOARD_ALLOWED_ORIGINS` contains `*`, the application raises during construction because credentialed CORS (required for dashboard API access) cannot safely use wildcards. Explicit origins enable credential sharing.

## Sandboxes: provider selection and configuration

`SANDBOX_TYPE` defaults to `langsmith` and selects the sandbox backend. The provider registry recognizes `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local`. Unsupported values raise `ValueError` with the supported list. The local provider executes commands on the host without isolation and is intended for local development only.

### LangSmith sandbox resources

For LangSmith, resource defaults are:
- 128 GiB filesystem (`DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES`)
- 4 vCPUs (`DEFAULT_SANDBOX_VCPUS`)
- 16 GiB memory (`DEFAULT_SANDBOX_MEM_BYTES`)
- 7200 seconds idle TTL (`DEFAULT_SANDBOX_IDLE_TTL_SECONDS`; 0 disables auto-stop)
- 2592000 seconds deletion-after-stop (`DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS`; 0 disables auto-delete)

These are deployment defaults when a workspace does not override them. Startup validation rejects non-integer values, negative TTLs, and invalid `SANDBOX_CREATE_EXTRA_JSON` (not JSON or not an object).

### Workspace sandbox customization

Each workspace can specify:
- `base_snapshot_id`: opaque provider-scoped snapshot identifier; new sandboxes boot from it instead of the default root snapshot
- `mem_bytes`, `vcpus`, `fs_capacity_bytes`: resource overrides for LangSmith (only; other providers ignore these)
- `create_params`: arbitrary LangSmith create-body fields (only; other providers ignore these)

The registry passes these only to the LangSmith factory; other providers receive only an existing sandbox ID if reconnecting.

### Snapshot naming and credentials

`WORKSPACE_SNAPSHOT_PREFIX` (default `openswe`, legacy alias `ENVIRONMENT_SNAPSHOT_PREFIX`) prefixes environment snapshot names, separating deployments that share a LangSmith workspace.

Sandbox operations use `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT` unless workspace-specific `SANDBOX_LANGSMITH_API_KEY` and `SANDBOX_LANGSMITH_ENDPOINT` are set. This allows a single workspace to use multiple LangSmith tenants for sandboxes.

## Model selection and defaults

`LLM_MODEL_ID` and `LLM_REASONING_EFFORT` establish the deployment fallback pair. The model must be in the catalog of selectable defaults; an unsupported value raises when the deployment default is resolved. The deployment default applies when no team, workspace, profile, or thread setting supplies a valid choice.

### Team settings

The team-settings record is a single instance-wide Store record (`default` key, `team_settings` namespace). It holds:
- Review toggles and guidelines (`review_draft_prs`, `pr_summaries`, `review_trace_links`, `org_guidelines`)
- Default model-and-effort pairs for agent, reviewer, chat, and thread titles
- Feature flags (`gateway_enabled`, `fable_enabled`, `expedited_review_enabled`, etc.)
- Default repository and transcription model

Input validation rejects unsupported model/effort combinations, an effort without a model, and oversized text. Deprecated model IDs are cleared; canonical pairs are normalized. When Fable is disabled, persisted Fable defaults are replaced with safe fallbacks.

Store reads for team settings are fail-soft (unavailable Store returns hardcoded defaults), because the agent and every webhook read settings on the run path. Invalid/stale model data falls back first to a supported model from the same provider, then to the global fallback. Chat inherits the agent default when unset, and review grouping inherits the reviewer subagent default.

### Fallback models and request timeouts

`LLM_FALLBACK_MODEL_ID` names an explicit fallback; otherwise Anthropic primaries fall back to `openai:gpt-6.1-sol` and OpenAI to `anthropic:claude-opus-5-5`. Fallback middleware is installed only when the fallback exists and differs from the primary. `make_model` constructs and caches clients per event loop and model options.

Each provider request receives up to 6 retries. OpenAI, Anthropic, Baseten, Google GenAI, and Fireworks also receive a 600-second default per-request timeout. These bounds complement the agent's separate model-call deadline and run recursion limits.

### Gateway routing

LangSmith LLM Gateway routing is applied centrally by `make_model`. The deployment default is: explicitly `LANGSMITH_GATEWAY_ENABLED` if set; otherwise routing is on when `LANGSMITH_GATEWAY_API_KEY` is set. A team setting is tri-state: `true`/`false` is authoritative, `null` inherits the deployment default.

Gateway authentication prefers `LANGSMITH_GATEWAY_API_KEY` and falls back to `LANGSMITH_API_KEY`. `LANGSMITH_GATEWAY_BASE_URL` (default `https://gateway.smith.langchain.com`) sets the host. `LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES` (default `true`) controls whether OpenAI keeps the Responses API through the gateway.

Only OpenAI, Anthropic, Baseten, Fireworks, and Google GenAI are routable. If routing is enabled but the provider is not routable or no LangSmith key is available, a warning is logged and the provider is called directly (not a run failure). Direct Baseten requires `BASETEN_API_KEY` when gateway routing is unavailable.

## Secrets and identity

Keep secrets in deployment configuration or appropriate encrypted stores; do not place user GitHub access tokens in environment variables. For LangSmith sandboxes, GitHub proxy rules mint an installation token at runtime and inject it transparently; sandbox processes see a placeholder instead.

### Key identity settings

- **GitHub App**: `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_WEBHOOK_SECRET`
- **Dashboard session**: `DASHBOARD_JWT_SECRET` signs session cookies and OAuth state
- **Token encryption**: `TOKEN_ENCRYPTION_KEY` (one or more Fernet keys, comma/newline-separated, newest-first). Encryption uses the first key; decryption tries all, enabling rotation without invalidating stored tokens. Missing keys or invalid ciphertext log and return empty string.
- **Admins**: `CONFIGURED_ADMINS` is a normalized comma-separated allowlist of GitHub logins and/or emails (empty = no admins)
- **Repository allowlist**: `ALLOWED_GITHUB_ORGS` and `ALLOWED_GITHUB_REPOS`. If both are empty, all repositories pass; otherwise an allowlisted owner or exact `owner/repo` is required.

### Run-completion webhooks

Run completion/failure replies require `RUN_COMPLETE_WEBHOOK_SECRET` and a usable `COMPLETION_WEBHOOK_URL`. The secret makes `/webhooks/run-complete` fail-closed: every call without a valid token is rejected, disabling completion replies until configured.

Dispatch attaches a completion webhook only when the secret is set and `COMPLETION_WEBHOOK_URL` is absolute (not relative) and non-loopback (not localhost or 127.0.0.1). A relative or loopback URL is logged as a warning and omitted (not a run failure) so invalid URLs cannot poison run creation. The dispatch URL carries the token as a query parameter for constant-time verification.

## Operation and extension

**Adding an environment variable**: Declare it in `openswe/config.py` with a description, defaults, secret status, and any aliases or deprecated names. Consume through `ENV.NAME` or `ENV["NAME"]`, never through direct `os.environ` reads, so late hydration and key rotation are observed.

**Persisted settings**: Use the Store only for instance-wide team settings and workspace-level base snapshot/custom resources. Design changes to degrade gracefully when Store reads fail (unavailable or missing data return hardcoded defaults). Chat inherits agent model defaults, and reviewer grouping inherits reviewer subagent defaults, unless overridden.

**Before deploying**: Exercise lifespan startup validation with the selected sandbox provider and model configuration. In local development, set an explicit `http://localhost...` `DASHBOARD_BASE_URL` to enable model-credential validation at boot.

**Run-completion replies**: Set `COMPLETION_WEBHOOK_URL` to the public HTTPS URL ending in `/webhooks/run-complete` and configure `RUN_COMPLETE_WEBHOOK_SECRET` with a strong token. Loopback URLs are omitted even when configured; relative URLs raise a warning.

**Token encryption rotation**: Prepend a new Fernet key to `TOKEN_ENCRYPTION_KEY` (comma/newline-separated, newest-first) without removing old keys. All stored tokens remain valid during the retention window. Remove old keys only after they have been re-encrypted or expired.

### Tests

Cover the registry's behavior (blank/alias/typed-value access), invalid LangSmith numeric and JSON fields during startup, workspace snapshot defaults and Store-failure fallbacks, model-pair normalization and inheritance, gateway precedence, and webhook edge cases (missing secret, non-public URL).

## See also

- [Deployment](deployment.md) — installation procedures
- [Models, profiles, and instructions](../concepts/models-profiles-instructions.md) — model selection and resolution
- [Authentication and security](../concepts/auth-and-security.md) — GitHub App, OAuth, and authorization
- [Sandbox providers](../integrations/sandbox-providers.md) — provider-specific details
