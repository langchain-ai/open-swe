---
type: operations reference
title: Runtime Configuration and Workspace Settings
description: Safe operational guide to Open SWE environment settings, startup checks, PostgreSQL and sandbox prerequisites, and the tiered instance and workspace settings surface.
tags: [configuration, operations, environment-variables, startup-validation, workspaces, sandbox, database]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-19T08:13:05.087Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-068d65a84c760eb8d555055e
    resource: repo://agent/completion.py
  - id: openwiki-source-b05c9910677cf23a9325276c
    resource: repo://agent/config.py
  - id: openwiki-source-5460c3972fe61bb256d07994
    resource: repo://agent/dashboard/oauth.py
  - id: openwiki-source-1a2ed68b81f13bbc58887d87
    resource: repo://agent/dashboard/workspace_settings_cache.py
  - id: openwiki-source-0a6d03ee63c0e527ce21bf77
    resource: repo://agent/dashboard/workspace_settings.py
  - id: openwiki-source-884d5b7fd1b37bffacbc2180
    resource: repo://agent/database/migrations/versions/0013_workspaces.py
  - id: openwiki-source-0dc2eaa9f468f4d742bc32b4
    resource: repo://agent/database/postgres.py
  - id: openwiki-source-c48b309c5ca416cf623f0866
    resource: repo://agent/dispatch.py
  - id: openwiki-source-eb53b48336d1b5fc0816441a
    resource: repo://agent/encryption.py
  - id: openwiki-source-2dedcea02c5aa03c54d81c32
    resource: repo://agent/sandboxes/providers/langsmith.py
  - id: openwiki-source-49bfbb811c25e99235121924
    resource: repo://agent/sandboxes/providers/registry.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-f0db445078d7a8158aa93724
    resource: repo://agent/utils/gateway.py
  - id: openwiki-source-56ade344fdbe7d47c84f008f
    resource: repo://agent/utils/model.py
  - id: openwiki-source-aebc62fe1f2d776d56ba1776
    resource: repo://agent/workspaces/refresh.py
  - id: openwiki-source-8b2e0e45c6159bcb1b873246
    resource: repo://agent/workspaces/store.py
  - id: openwiki-source-8010c6e64af5a375d8d3b70b
    resource: repo://docs/CUSTOMIZATION.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
generated: { by: "openwiki/0.4.2", at: "2026-09-19T08:13:05.087Z" }
---

# Runtime Configuration and Workspace Settings

Open SWE separates **deployment configuration** from **administrator-managed settings**:

- Environment variables, declared in `agent/config.py`, provide credentials, endpoints, provider selection, and deployment-wide defaults. `langgraph.json` loads `.env` for a LangGraph deployment.
- The LangGraph Store holds the instance settings record and sparse workspace overrides for behavior such as model defaults, review options, gateway routing, and organization guidelines.
- PostgreSQL is the durable authority for workspaces, repository/channel bindings, users, and pull-request records. It is not optional at runtime.

This page describes the control points and their precedence rather than reproducing every variable. See [deployment](deployment.md), [authentication and security](../concepts/auth-and-security.md), [models, profiles, and instructions](../concepts/models-profiles-instructions.md), and [sandbox providers](../integrations/sandbox-providers.md) for domain-specific details.

## Environment registry

`ENV` is the configuration schema and should be the only application path for reading environment values. Each `EnvVar` is read lazily, so secret injection after import, key rotation, and test monkeypatches take effect on the next read. Whitespace-only values are unset. A declaration can carry a default, aliases, a secret marker, and deprecation metadata.

The canonical variable wins over an alias. `optional()` deliberately ignores the declared default, whereas `get()` uses a caller-provided fallback first and then the declaration default. Integer parsing raises for malformed values; boolean parsing recognizes `1/true/yes/on` and `0/false/no/off` and otherwise returns its supplied default; lists are comma-separated, trimmed, and omit empty entries. Accessing an undeclared registry name fails, making a new variable an explicit schema change. `deprecated_in_use()` reports legacy aliases and deprecated names, except where a replacement is also configured.

Operationally, add a setting to `agent/config.py`, mark credentials `secret=True`, and consume it through `ENV`; do not introduce another literal environment read.

## Application topology and startup gates

`langgraph.json` registers the `agent`, `reviewer`, `analyzer`, `chat`, and `scheduler` graphs and mounts `agent.webapp:app` as the HTTP app. Its checkpointer deletes expired data on a 60-minute sweep with a default TTL of 43200 minutes (30 days).

The FastAPI lifespan is deliberately a hard gate for unsafe prerequisites. It pins the process to one event loop, rejects a deployment without a GitHub login allowlist unless local-token auth is enabled, validates the active sandbox configuration, validates localhost-development model credentials, requires PostgreSQL, and applies migrations. It then attempts legacy Store imports and analytics startup; those latter operations log failures rather than taking the application down. Shutdown stops analytics, disposes the database engine, and closes cached model clients.

```mermaid
flowchart TD
    Init["Create application"] --> Loop["Pin one event loop"]
    Loop --> Lifespan["Start lifespan"]
    Lifespan --> Gate["Validate login, sandbox, model, and PostgreSQL"]
    Gate --> Migrate["Migrate open_swe schema"]
    Migrate --> Import["Import legacy Store records"]
    Import --> Serve["Serve requests"]
    Gate --> Abort["Raise and abort startup"]
    Serve --> Close["Stop worker and close database and models"]
```

The diagram shows the mandatory startup gates and best-effort transition work. Workspace import failure is intentionally not a normal-success condition: until it succeeds, repository routing fails closed rather than treating an unimported legacy workspace as unowned.

`DASHBOARD_ALLOWED_ORIGINS` enables credentialed CORS only when nonempty. It must not contain `*`; application construction raises because wildcard origins are incompatible with credentialed CORS.

## PostgreSQL prerequisite and lifecycle

`POSTGRES_URI` is mandatory because pull-request, repository, user, workspace, and binding records are stored there. In the `local_dev` LangGraph variant, an unset value falls back to `postgresql://postgres:postgres@127.0.0.1:5433/postgres`; other deployments fail startup without it. The URI accepts `postgres://` or `postgresql://`, normalizes them to asyncpg, translates `sslmode` to `ssl`, and rejects non-PostgreSQL schemes or simultaneous SSL forms.

At startup, migrations run under a PostgreSQL advisory transaction lock and create/use the `open_swe` schema. Connections set that schema before use; the reusable engine has pre-ping plus pool size, overflow, and checkout timeout controlled by the analytics pool settings. A changed URI causes a new engine, and shutdown disposes it.

Workspaces and their bindings live in PostgreSQL. The binding tables make repository and Slack-channel ownership unique at the database level: one repository or channel cannot belong to more than one workspace. A one-time-compatible startup import copies legacy Store workspace records only when valid, removes successfully handled records, and leaves invalid or conflicting records for retry while preventing their repositories from being routed as unowned.

## Sandbox selection and workspace images

`SANDBOX_TYPE` defaults to `langsmith`. `create_sandbox()` lazily resolves it through the registry: `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local` are supported. An unknown type raises `ValueError` with the supported values. Provider-specific arguments—snapshot ID, memory, vCPUs, filesystem capacity, and extra create parameters—are passed only to LangSmith; other factories receive only an optional existing sandbox ID. The local backend executes on the host and is therefore suitable only for trusted local development.

For LangSmith, sandbox API operations use `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`; missing key material fails when the provider is constructed. The provider defaults new-sandbox capacity to 128 GiB filesystem, 4 vCPUs, 16 GiB memory, 7200 seconds idle TTL, and 2592000 seconds delete-after-stop. Set either TTL to `0` to disable that expiration behavior. Startup validation for `SANDBOX_TYPE=langsmith` rejects non-integer resource or TTL values, negative TTLs, and malformed or non-object `SANDBOX_CREATE_EXTRA_JSON`. Other provider configuration is not validated by this startup hook.

A workspace, rather than a global administrator snapshot setting, owns its optional `base_snapshot_id`, resource overrides, scripts, and create parameters. Its snapshot configuration applies to its builder/run path; no usable snapshot means LangSmith starts from its root snapshot. A full workspace refresh boots a throwaway builder from the base snapshot, runs setup then update scripts, and captures only when all scripts succeed. An update refresh instead starts from the currently ready snapshot and runs only the update script. Platform TTL/reclamation is responsible for deleting sandboxes: application code intentionally has no delete operation because a sandbox can hold the only working tree.

## Settings surfaces and precedence

The instance record remains at Store namespace `team_settings`, key `default`, for compatibility. It is the middle tier between hardcoded defaults and one sparse Store record per workspace in `workspace_settings`. A workspace field that is absent or `None` inherits the instance value; an instance `None` falls back to a hardcoded value. Existing legacy per-workspace records beside the instance record are read until the workspace is saved again, then removed.

```mermaid
flowchart TD
    Hardcoded["Hardcoded defaults"] --> Instance["Instance Store record"]
    Instance --> Workspace["Workspace sparse overrides"]
    Workspace --> Caller["Profile and thread layers in callers that honor them"]
    Caller --> Effective["Effective run setting"]
```

The diagram is the settings-resolution order. The runtime resolves a requested workspace after slugification, otherwise the current run's workspace, otherwise `default`. Reads are fail-soft: unavailable Store data returns hardcoded defaults rather than stopping every agent, reviewer, or webhook run. Graph factories cache the resolved value for 60 seconds with the workspace slug in the cache key, preventing one workspace's values from leaking to another.

The API exposes the instance record at `GET /settings` and admin-only `PUT /settings`; `/team-settings` remains a hidden compatibility alias. `GET /workspaces/{workspace}/settings` returns both `effective` values and the workspace's explicit `overrides`; admin-only `PUT` replaces those overrides. The workspace must exist, and invalid slugs receive 400 while unknown valid slugs receive 404.

Settings include review toggles, organization guidelines, a default repository, model/effort pairs for agent, reviewer, subagents, review chat, diff grouping, thread titles, adaptive routing tiers, the LLM Gateway toggle, and Fable/expedited-review toggles. Inputs validate supported model-and-effort pairs, reject an effort without a model, normalize canonical model pairs, clear deprecated models, and cap guidelines at 10,000 characters. Chat inherits the agent default when unset; diff grouping inherits the reviewer subagent default. Invalid stale defaults try a same-provider fallback before the global default. Disabling Fable replaces saved Fable defaults with safe fallbacks, so it acts as a kill switch.

## Model and gateway configuration

`LLM_MODEL_ID` and `LLM_REASONING_EFFORT` provide deployment defaults beneath persisted settings and more-specific profile/thread/run selections. `make_model()` applies six retries to every constructed model and a 600-second per-request timeout to OpenAI, Anthropic, Baseten, Google GenAI, and Fireworks. It caches clients by event loop and model options; lifespan shutdown closes the cache. `LLM_FALLBACK_MODEL_ID` names an explicit fallback, otherwise Anthropic and OpenAI primaries have cross-provider fallback IDs; fallback middleware is used only when the fallback differs from the primary.

Gateway routing is deployment-controlled by `LANGSMITH_GATEWAY_ENABLED` when set; otherwise a configured `LANGSMITH_GATEWAY_API_KEY` enables it. The resolved workspace setting is tri-state: `true` and `false` override that deployment default, while unset inherits it. Gateway authentication prefers the dedicated gateway key and falls back to `LANGSMITH_API_KEY`. Only supported provider routes receive gateway overrides; an unsupported route or absent LangSmith key is logged and remains a direct provider call rather than failing a run.

## Secrets and completion callbacks

Keep provider credentials and signing/encryption keys in deployment configuration; do not put per-user GitHub tokens in environment variables. `TOKEN_ENCRYPTION_KEY` accepts one Fernet key or a comma/newline-separated newest-first list. Encryption uses the first key and decryption attempts every key, which permits staged rotation: prepend the new key, keep old keys until stored secrets have been re-encrypted or retired, then remove them.

`RUN_COMPLETE_WEBHOOK_SECRET` protects `/webhooks/run-complete` by constant-time token comparison and fails closed when absent. Dispatch attaches no completion webhook without that secret, or when `COMPLETION_WEBHOOK_URL` is relative, schemeless, or loopback, because the platform rejects such callbacks and would otherwise reject run creation. Configure an absolute public `https` URL ending in `/webhooks/run-complete` and the secret to enable completion and failure replies.

## Focused verification

Changes in this area should exercise the existing focused tests:

- `tests/utils/test_config.py` covers lazy registry semantics, aliases, blanks, types, and undeclared names.
- `tests/sandbox/test_langsmith_sandbox_config.py` covers LangSmith defaults, overrides, zero TTLs, and invalid startup values.
- `tests/dashboard/test_workspace_settings_tiers.py` covers inheritance, legacy records, sparse overrides, cache isolation, validation, and HTTP error behavior.
- PostgreSQL integration tests should verify URI normalization, migrations, schema search paths, advisory-lock behavior, and exclusive workspace bindings.

## See also

- [Deployment](deployment.md)
- [Authentication and security](../concepts/auth-and-security.md)
- [Models, profiles, and instructions](../concepts/models-profiles-instructions.md)
- [Sandbox providers](../integrations/sandbox-providers.md)
