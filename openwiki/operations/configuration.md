---
type: operations reference
title: Configuration and Startup Validation
description: Explains Open SWE's lazy environment registry, persisted administrator settings, model and sandbox selection, secrets, and the validation that can stop a server from starting.
tags: [configuration, operations, environment-variables, startup-validation, sandbox, models, security]
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
  - id: openwiki-source-cbab46b11893a9efc599e687
    resource: repo://openswe/utils/gateway.py
  - id: openwiki-source-4cc74089c0207ec1e5a6ca3b
    resource: repo://openswe/utils/model.py
  - id: openwiki-source-7b35cf61ea1491240ef4c804
    resource: repo://openswe/workspaces/store.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T15:19:10.971Z" }
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T15:19:10.971Z
---

# Configuration and Startup Validation

Open SWE has two configuration planes:

- **Deployment configuration** is declared centrally in `openswe/config.py` and normally supplied through environment variables (including the `.env` file named by `langgraph.json`). It covers connectivity, credentials, provider selection, UI URLs, and deployment defaults.
- **Administrator settings** are instance-wide records in LangGraph Store. They change selected behavior without a redeploy, notably team-wide review/model settings. Workspace-level settings (base snapshot, custom resource sizing) are stored per-workspace. These are not substitutes for secrets or provider credentials, which remain deployment configuration.

This page describes ownership and failure behavior rather than listing every variable. `openswe/config.py` is the complete variable catalog; [deployment](deployment.md) provides installation procedures. See [models, profiles, and instructions](../concepts/models-profiles-instructions.md), [auth and security](../concepts/auth-and-security.md), and [sandbox providers](../integrations/sandbox-providers.md) for their respective domains.

## Configuration ownership and value semantics

`ENV` is the single registry for application configuration. Code reads values through an `EnvVar` object rather than taking an import-time snapshot of `os.environ`: reads are lazy, so late secret hydration, key rotation, and test monkeypatching can be observed. Whitespace-only values count as unset. The registry also records descriptions, defaults, secret classification, aliases, and deprecated names; its typed accessors parse comma-separated lists, integers, and conventional boolean strings.

The canonical name takes precedence over aliases. Deprecated aliases are centralized in the registry, rather than scattered through consumers; `deprecated_in_use()` can report an alias or deprecated setting, suppressing a deprecated warning when its replacement is also set. An undeclared `ENV.NAME` or `ENV["NAME"]` is an error, which makes adding a variable an explicit configuration-schema change.

### Deployment topology

`langgraph.json` registers six graphs (`agent`, `reviewer`, `analyzer`, `review-scout`, `chat`, and `scheduler`) and mounts `openswe.webapp:app` as the platform HTTP application for dashboard and webhook routes. Its checkpointer uses delete-based TTL cleanup: a 60-minute sweep and a default TTL of 43200 minutes (30 days). The file names `.env` as its environment file.

## Startup lifecycle and failures

The FastAPI composition entrypoint is `openswe.api.app:create_app`. It pins the process to a single event loop before queue work is constructed and again in lifespan startup. The lifespan validates the configured sandbox provider, local-development model credentials, and GitHub login authorization; then yields only if all succeed. On shutdown it closes the database connection and stops background workers.

```mermaid
flowchart TD
    Init["Import application"] --> Pin["Pin one event loop"]
    Pin --> Build["Create FastAPI application"]
    Build --> Start["Lifespan startup"]
    Start --> CheckAuth["Validate GitHub login allowlist"]
    CheckAuth --> CheckSandbox["Validate active sandbox configuration"]
    CheckSandbox --> CheckModel["Validate localhost model credential"]
    CheckModel --> Migrate["Run non-blocking migrations"]
    Migrate --> Serve["Serve routes and runs"]
    CheckAuth --> Stop["Raise and abort startup"]
    CheckSandbox --> Stop
    CheckModel --> Stop
    Serve --> Close["Close database and stop workers"]
```

The diagram shows the boot-time checks performed by the FastAPI lifespan and cleanup on shutdown.

Not every bad setting is checked at boot. The sandbox validator is provider-specific: it currently validates LangSmith resource fields and extra JSON only when `SANDBOX_TYPE=langsmith`; an unknown provider name is rejected when the registry is asked to create a sandbox. Likewise, model startup validation deliberately applies only when an explicitly configured `DASHBOARD_BASE_URL` starts with `http://localhost`; it checks the credential needed by the deployment's `LLM_MODEL_ID` (or default) and does not attempt to validate models that may later be chosen through team, profile, or thread settings.

GitHub login allowlist validation requires that when no local-auth token is configured, either `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` is set; startup fails closed if neither is present and the deployment is not in local-auth-only mode.

`DASHBOARD_ALLOWED_ORIGINS` is separately checked while the application is built: if it contains `*`, construction raises because credentialed CORS cannot safely use a wildcard. Nonempty explicit origins enable credentialed CORS for the dashboard API.

## Sandboxes: provider, resources, and workspace snapshots

`SANDBOX_TYPE` defaults to `langsmith`. The lazy provider registry maps `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local` to provider factories. An unsupported value raises `ValueError` and lists the supported values. The local provider executes on the host and has no isolation, so it is for local development rather than untrusted work.

For LangSmith sandboxes, resource defaults are 128 GiB filesystem (`DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES`), 4 vCPUs (`DEFAULT_SANDBOX_VCPUS`), 16 GiB memory (`DEFAULT_SANDBOX_MEM_BYTES`), 7200 seconds idle TTL (`DEFAULT_SANDBOX_IDLE_TTL_SECONDS`; 0 disables), and 2592000 seconds deletion-after-stop (`DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS`; 0 disables). These are deployment defaults applied when a workspace does not specify custom values. Malformed numeric resource values, negative TTLs, and invalid/non-object `SANDBOX_CREATE_EXTRA_JSON` fail LangSmith startup validation.

Each workspace can specify a `base_snapshot_id` (an opaque provider-scoped identifier), custom resource overrides (`mem_bytes`, `vcpus`, `fs_capacity_bytes`), and arbitrary provider create-body parameters (`create_params`). When a workspace has captured a snapshot, new sandboxes boot from the captured snapshot instead. The registry passes snapshot_id, resource overrides, and create parameters only to the LangSmith factory; other providers receive only an optional existing sandbox ID.

`WORKSPACE_SNAPSHOT_PREFIX` (default `openswe` via legacy alias `ENVIRONMENT_SNAPSHOT_PREFIX`) separates environment snapshot names when deployments share a LangSmith workspace. Sandbox operations use the main `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT` unless workspace-specific overrides are present. This allows a single workspace to use multiple LangSmith tenants for sandboxes by setting `SANDBOX_LANGSMITH_API_KEY` and `SANDBOX_LANGSMITH_ENDPOINT`, which sandbox API operations, proxy setup, and environment snapshot work will prefer; credentials fall back to their standard counterparts when the sandbox-specific ones are absent.

## Models and team settings

`LLM_MODEL_ID` and `LLM_REASONING_EFFORT` establish the deployment fallback pair. Resolution accepts only catalog models allowed as defaults and validates that the effort is supported; an unsupported model or effort raises when defaults are resolved. The deployment default is used when no durable team setting supplies a valid choice. Model selection can be more specific at team, profile, thread, or run scope; changing the environment does not overwrite existing selections.

The team-settings record is a single instance-wide Store record keyed `default`. It includes review toggles, organization guidelines, review trace project, default repository, transcription model, gateway toggle, Fable opt-in, and main/subagent model-and-effort pairs for agent and reviewer, plus chat and thread title settings. Input validation rejects unsupported model/effort combinations, an effort without a model, invalid transcription identifiers, and oversized guidelines or trace-project text. Deprecated model IDs are cleared and canonical pairs are normalized. When Fable is disabled, persisted Fable defaults are replaced with safe fallback pairs.

Store reads for team settings are fail-soft because model selection is on the run path: unavailable or absent Store data returns hardcoded defaults. Invalid/stale model data resolves first to a supported model from the same provider where possible, then to the global fallback. Chat inherits the agent default when unset, and review grouping inherits the reviewer subagent default.

`DEFAULT_LLM_MAX_TOKENS` is 64000 and is an output/completion budget, not a context-window size. `LLM_FALLBACK_MODEL_ID` can name a fallback; otherwise Anthropic and OpenAI primary models have cross-provider defaults. Fallback middleware is installed only if the fallback exists and differs from the selected primary. `make_model` caches constructed clients per running event loop and model options.

Each supported direct provider request receives up to six retries; OpenAI, Anthropic, Baseten, Google GenAI, and Fireworks also receive a 600-second default request timeout. These bounds complement the agent's separate model-call deadline and run recursion limits described in [models, profiles, and instructions](../concepts/models-profiles-instructions.md).

### Gateway precedence

LangSmith LLM Gateway routing is centrally applied by `make_model`. `LANGSMITH_GATEWAY_ENABLED` is authoritative when set; otherwise setting `LANGSMITH_GATEWAY_API_KEY` turns routing on. A team setting is tri-state: `true` or `false` overrides this deployment default, while unset inherits it. Gateway authentication prefers `LANGSMITH_GATEWAY_API_KEY` and falls back to `LANGSMITH_API_KEY`; `LANGSMITH_GATEWAY_BASE_URL` changes the gateway host and `LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES` defaults to true.

Only OpenAI, Anthropic, Baseten, Fireworks, and Google GenAI have gateway routes. If routing is enabled but the provider is not routable, or no LangSmith key is available, the code logs a warning and calls the provider directly rather than failing the run. Direct Baseten calls are stricter: with gateway routing unavailable, `BASETEN_API_KEY` is required.

## Secrets, identity, and completion delivery

Keep secrets in deployment configuration or the encrypted credential stores appropriate to their integration; do not place user GitHub access tokens in deployment environment variables. For LangSmith sandboxes, GitHub proxy rules mint an installation token at runtime and inject it on the wire for `github.com` and `api.github.com`; sandbox processes see placeholders rather than the real credential.

Important identity settings include GitHub App credentials (`GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, installation ID, client ID/secret, and webhook secret), Slack signing and bot credentials, dashboard cookie/OAuth-state signing (`DASHBOARD_JWT_SECRET`), and `TOKEN_ENCRYPTION_KEY`. The latter accepts one Fernet key or a comma/newline-separated newest-first list: encryption uses the first key and decryption tries all keys, permitting key rotation. Encryption fails when a key is absent; decryption logs and returns an empty string for missing keys or invalid ciphertext.

`CONFIGURED_ADMINS` is a normalized, comma-separated allowlist of GitHub logins and/or emails; with an empty value, no dashboard identity is an admin. `OBSERVABILITY_AUTHORIZED_EMAILS` separately grants the read-only observability tools, while configured admins are always authorized. Repository allowlists use `ALLOWED_GITHUB_ORGS` and `ALLOWED_GITHUB_REPOS`: if both are empty, any repository passes; otherwise an allowlisted owner or exact `owner/repo` is required.

Run-completion replies require both a secret and a usable delivery URL. `RUN_COMPLETE_WEBHOOK_SECRET` makes `/webhooks/run-complete` fail closed: every callback is rejected without it. Dispatch attaches a callback only when the secret exists and `COMPLETION_WEBHOOK_URL` is absolute and non-loopback; a relative or loopback URL is warned about and omitted so it cannot make run creation fail. The dispatch URL carries the token query parameter for the completion endpoint's constant-time comparison.

## Operating guidance

1. Declare new environment variables in `openswe/config.py`, including whether they are secret and any alias/deprecation relationship; consume them through `ENV` rather than a new direct environment read.
2. Treat environment values as deployment defaults and credentials. Use the dashboard's persisted settings only for the explicitly supported instance-wide choices (team settings) and workspace-level choices (base snapshot, custom sizing), and account for their fail-soft Store reads when designing changes.
3. Before rollout, exercise lifespan startup with the selected sandbox and model configuration. In local development, set an explicit `http://localhost...` `DASHBOARD_BASE_URL` to activate model-key validation.
4. Configure `COMPLETION_WEBHOOK_URL` as the public HTTPS `.../webhooks/run-complete` URL together with `RUN_COMPLETE_WEBHOOK_SECRET` if completion/failure replies are required.
5. Rotate `TOKEN_ENCRYPTION_KEY` by prepending a new valid key while retaining old keys, then remove old keys only after stored tokens have been re-encrypted or retired.

Focused tests should cover the registry's blank/alias/typed-value behavior, invalid LangSmith numeric and JSON settings during lifespan startup, workspace snapshot defaults and Store failure fallback, model-pair normalization/inheritance, gateway precedence, and the completion webhook's missing-secret and non-public-URL cases.

## See also

- [Deployment](deployment.md)
- [Authentication and security](../concepts/auth-and-security.md)
- [Models, profiles, and instructions](../concepts/models-profiles-instructions.md)
- [Sandbox providers](../integrations/sandbox-providers.md)
- [Observability and MCP](../integrations/observability-and-mcp.md)
