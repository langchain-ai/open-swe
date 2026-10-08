---
type: operations reference
title: Configuration, Workspaces, and Persistence
description: Explains configuration ownership and precedence across deployment environment, persisted instance and workspace settings, PostgreSQL records, startup migrations, workspace routing, models, and sandbox providers.
tags: [configuration, operations, workspaces, persistence, migrations, sandbox, models, security]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-08T08:17:40.044Z
sources:
  - id: openwiki-source-e201e686a785f09b6d899f0b
    resource: repo://compose.yaml
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-8edaced2842e8bdf5ec72158
    resource: repo://openswe/dashboard/options.py
  - id: openwiki-source-775d5704fff1c9b4f3e91941
    resource: repo://openswe/dashboard/workspace_settings.py
  - id: openwiki-source-2bc4d5316b7482c6736d32cf
    resource: repo://openswe/database/migrations/versions/0013_workspaces.py
  - id: openwiki-source-a4666bd3c4527cb242b50ab4
    resource: repo://openswe/database/migrations/versions/0042_ef05fbc2c85a_seed_the_default_workspace.py
  - id: openwiki-source-a13697e04823548408653de5
    resource: repo://openswe/database/postgres.py
  - id: openwiki-source-b11ec0af4e40439361058935
    resource: repo://openswe/encryption.py
  - id: openwiki-source-d16a45e9fc6aa80a3708c88c
    resource: repo://openswe/sandboxes/providers/langsmith.py
  - id: openwiki-source-a4c632cb1c0a9a7a637ab9fe
    resource: repo://openswe/sandboxes/providers/registry.py
  - id: openwiki-source-8a63971e6f57fbbd7583054b
    resource: repo://openswe/schedules/store.py
  - id: openwiki-source-3bb2edf1821874061db6afe7
    resource: repo://openswe/users/import_store.py
  - id: openwiki-source-cbab46b11893a9efc599e687
    resource: repo://openswe/utils/gateway.py
  - id: openwiki-source-85843ede883de0893511a050
    resource: repo://openswe/workspaces/routing.py
  - id: openwiki-source-7b35cf61ea1491240ef4c804
    resource: repo://openswe/workspaces/store.py
  - id: openwiki-source-4c7e8fce34b1ca4c14da5aba
    resource: repo://tests/github/test_github_workspace_routing.py
generated: { by: "openwiki/0.4.2", at: "2026-10-08T08:17:40.044Z" }
---

Open SWE separates values by **owner** rather than treating configuration as one environment-variable list:

1. **Deployment configuration** is declared in `openswe/config.py` and supplied through the process environment. It owns connectivity, credentials, provider choice, resource defaults, and global fallbacks.
2. **Persistent instance and workspace settings** are administrator-managed behavior. Instance settings live in the LangGraph Store; a workspace can override only selected fields. User profiles and per-thread/run configuration can be more specific where a caller honors them.
3. **PostgreSQL records** own durable multi-row domain state, including workspaces and their repository and Slack bindings. The server migrates this database at boot.

This page explains precedence, lifecycle, and compatibility rather than reproducing every variable. See [Deployment](deployment.md) for rollout procedures, [Authentication and security](../concepts/auth-and-security.md) for access controls, [Models, profiles, and instructions](../concepts/models-profiles-instructions.md) for selection at run time, and [Sandbox providers](../integrations/sandbox-providers.md) for provider-specific behavior.

## Deployment configuration: registry and compatibility

`ENV` is the application’s centralized environment registry. Consumers read `ENV.NAME` lazily instead of snapshotting `os.environ` during import, so late secret hydration, rotations, and test changes are visible. Whitespace-only values are unset. Each declaration documents its default, secret status, aliases, and deprecation relationship; typed accessors parse integers, conventional booleans, and comma-separated lists. Accessing an undeclared name is an error, so adding a setting is an explicit schema change.

The canonical name wins over aliases. `ENV.deprecated_in_use()` reports aliases and obsolete names, but suppresses an obsolete-name warning when the replacement is set. Compatibility aliases currently preserve the former environment terminology: `ENVIRONMENT_SNAPSHOT_PREFIX` feeds `WORKSPACE_SNAPSHOT_PREFIX`, and the former refresh, update, and capture timeout names feed their `WORKSPACE_*` replacements. Use the current names in new deployments and code.

### Deployment defaults are not persisted choices

Environment model values (`LLM_MODEL_ID`, `LLM_REASONING_EFFORT`), the gateway default, sandbox provider and resource values, and repository defaults are fallbacks. They do not overwrite administrator, workspace, profile, thread, or run selections already stored elsewhere. Environment credentials remain deployment-owned: never place user access tokens in workspace settings or scripts.

`TOKEN_ENCRYPTION_KEY` protects stored sensitive values. It accepts one Fernet key or a comma/newline-delimited newest-first key list. Encryption uses the first key and decryption tries the list in order, permitting a staged key rotation: prepend a new key, retain old keys until data has been re-encrypted or retired, then remove them. Encryption cannot proceed without a configured key; decryption logs and yields an empty value for missing-key or invalid-token cases.

## Startup, database, and migrations

The FastAPI lifespan validates prerequisites before serving and owns initialization and shutdown. At startup it pins the event loop; validates the GitHub login allowlist, sandbox configuration, and local-development model configuration; requires PostgreSQL; then runs migrations. It also attempts Store-to-PostgreSQL compatibility imports. Failures importing legacy users, concierge preferences, or automations are logged but do not abort startup; failures in the validation or database/migration stage do. Analytics and cross-process listeners are deliberately best-effort. Shutdown stops those services and disposes the database engine.

```mermaid
flowchart TD
    Boot["FastAPI lifespan starts"] --> Validate["Validate login, sandbox, and local model settings"]
    Validate --> Database["Require PostgreSQL"]
    Database --> Migrate["Lock and apply migrations"]
    Migrate --> Import["Attempt legacy Store imports"]
    Import --> Services["Start optional workers and listeners"]
    Services --> Serve["Serve requests"]
    Serve --> Stop["Stop services and close database engine"]
    Validate --> Abort["Validation failure aborts startup"]
    Database --> Abort
    Migrate --> Abort
```

This is the lifespan ordering: legacy imports and optional services occur only after the database is usable; startup-critical validation and migration failures prevent serving.

### PostgreSQL contract

`POSTGRES_URI` is mandatory in normal operation because repository and pull-request records are PostgreSQL-backed. In the `local_dev` LangGraph API variant only, an absent URI falls back to the Compose database at `postgresql://postgres:postgres@127.0.0.1:5433/postgres`. The URI normalizer accepts `postgres://` and `postgresql://` forms, converts them to the asyncpg dialect, and rejects non-PostgreSQL schemes or conflicting `ssl`/`sslmode` parameters.

All application tables live in the `open_swe` schema. The async engine enables connection pre-ping and uses configured pool sizing; connections set that schema as their search path. Slow-query logging is controlled by `POSTGRES_SLOW_QUERY_MS`, where zero disables it. `compose.yaml` supplies a development PostgreSQL 16 service whose data is a named volume and whose exposed port is loopback-only.

Migration execution is serialized across replicas with a PostgreSQL advisory transaction lock. It creates the schema if needed, finds Alembic revisions under `openswe/database/migrations`, and upgrades to all heads transactionally. `OPENSWE_ENV=preview` has a narrowly scoped compatibility behavior: before upgrading it removes stamped revisions made superseded by a rebased preview branch. Do not use that preview-only revision repair as a production migration procedure.

### Store-to-database migration compatibility

Startup retains temporary importers for data that older releases stored in LangGraph Store. Legacy `user_mappings` become `users` rows; a mapping is deleted only after a complete, authorized import, while unreadable or unresolved mappings remain for a future startup. Legacy automations similarly copy into PostgreSQL with their identifiers retained so existing cron references remain valid; records that cannot be read remain, while records whose workspace no longer exists are removed with their obsolete cron data. These imports are intended to be idempotent and become no-ops after their source namespaces are empty.

## Workspaces: durable ownership and routing

A workspace is a PostgreSQL record that groups repositories, Slack channels, prompts, sandbox construction inputs, snapshot/refresh state, and settings overrides. Repository and Slack bindings are separate tables keyed by the bound resource, so the database—not application convention—enforces that each repository and channel has at most one owning workspace. A concurrent conflicting write becomes a readable conflict error. The default workspace is seeded by a migration and cannot be treated as an optional routing target.

Workspace definitions validate and normalize names into lower-case slugs, normalize repository names, limit bound repositories and channels, and require kitchen channels to also be bound Slack channels. Workspace `create_params` are valid bounded JSON but reject credential-like keys and sensitive headers. This protects the persisted sandbox-create passthrough from becoming a secret store.

### Resolution precedence and failure policy

Inbound work must use the routing module rather than independently guessing a workspace. A thread’s existing workspace is authoritative. For new work, a valid `workspace:<slug>` tag (the legacy `env:<slug>` spelling is accepted by the tag parser) is considered first, followed by a bound Slack channel, a repository owner, a user’s saved default workspace, and finally `default`. Channel binding outranks repository binding because a workspace channel must keep its conversation in that workspace even when repositories are shared candidates.

```mermaid
flowchart TD
    Thread["Existing thread workspace"] --> HasThread{"Present"}
    HasThread -->|"yes"| ThreadResult["Use thread workspace"]
    HasThread -->|"no"| Tag["Valid workspace tag"]
    Tag --> HasTag{"Bound workspace"}
    HasTag -->|"yes"| TagResult["Use tagged workspace"]
    HasTag -->|"no"| Channel["Bound Slack channel"]
    Channel --> HasChannel{"Owner found"}
    HasChannel -->|"yes"| ChannelResult["Use channel workspace"]
    HasChannel -->|"no"| Repo["Repository owner"]
    Repo --> HasRepo{"Owner found"}
    HasRepo -->|"yes"| RepoResult["Use repository workspace"]
    HasRepo -->|"no"| User["User default workspace"]
    User --> HasUser{"Existing workspace"}
    HasUser -->|"yes"| UserResult["Use user workspace"]
    HasUser -->|"no"| Default["Use default workspace"]
```

This is the routing precedence for ordinary resolution after the existing-thread check.

The failure rule depends on whether a delivery can be retried. Ordinary resolution and helper lookups log a Store/database lookup failure and fall back to `default`, favoring a run over no run. GitHub webhook admission does the opposite: `repo_is_routable()` propagates an unreadable ownership lookup so its route can return 503 and GitHub can retry. When ownership is readable but absent, `OPEN_SWE_UNASSIGNED_REPO_WORKSPACE=ignore` drops the event; `default` (and invalid values) permits it to route to the default workspace.

### Sandbox state owned by a workspace

A workspace can define a prompt, setup/update scripts, base snapshot, resource overrides, and nonsensitive create parameters. Refresh code builds a snapshot using the scripts; snapshot state records whether it is absent, capturing, ready, or failed. Runs use the immutable stored snapshot ID only when the state is ready or capturing. A refresh leaves the prior ready ID usable until the new capture succeeds, preventing a refresh in progress from sending new runs to a bare base image.

The workspace snapshot name defaults to `<prefix>-environment-<slug>`, retaining the historic `environment` infix for compatibility. `WORKSPACE_SNAPSHOT_PREFIX` defaults to `openswe`; a colon in its value is rejected operationally by falling back to that default because it would be interpreted as a snapshot tag separator. Setup/update scripts and their logs live under `OPENSWE_SCRIPT_ROOT`, defaulting to `/open-swe/environment`; scripts receive the space-delimited repository list in `OPENSWE_WORKSPACE_REPOS`. Do not put secrets in scripts: `bash -x` output is captured in refresh logs.

## Settings precedence: instance, workspace, profile, and run

Settings are sparse, layered records—not environment-variable overrides:

| Layer | Owner and storage | Effect |
|---|---|---|
| Hardcoded and deployment fallback | Code and `ENV` | Supplies safe defaults, including the deployment model pair and gateway default. |
| Instance settings | LangGraph Store `['team_settings']`, key `default` | Applies to every workspace. The old key is deliberately retained, so the former team-settings record needs no data migration. |
| Workspace settings | LangGraph Store `['workspace_settings', <slug>]` | A field absent or `null` inherits the instance value. |
| Personal profile and preferences | Profile/Store and user-preferences records | Callers that support them layer a user’s choices above the workspace. |
| Thread/run configuration | Runtime `configurable` data | The most-specific choice in callers that honor it. |

Effective workspace settings are merged as hardcoded defaults, instance record, then workspace overrides. Reads are deliberately fail-soft: a Store outage yields hardcoded defaults because settings are on the agent, reviewer, and webhook run path. The settings API exposes both `effective` values and a workspace’s own `overrides`, so an administrator can distinguish inheritance from an explicit value. The former `/team-settings` routes remain hidden aliases for the instance `/settings` routes; saving a workspace override also clears the legacy per-workspace record formerly kept in the instance namespace.

Supported settings include review and feature toggles, guidelines, default repository, LLM gateway, Fable, sandbox OpenAI, human-review timing, and model/effort pairs for agent, reviewer, subagents, routing tiers, chat, and titles. Updates reject an effort without a model, unknown models, and incompatible model/effort pairs; stale deprecated pairs are cleared. A missing or invalid persisted pair resolves to a supported same-provider fallback where possible, then the deployment fallback. Chat inherits the agent default when no valid chat-specific pair exists. Fable is a workspace-aware kill switch: when disabled, Fable defaults are replaced with non-Fable fallbacks both while saving and before model construction.

## Models and gateway configuration

`LLM_MODEL_ID` and `LLM_REASONING_EFFORT` provide the bottom deployment model pair. If no model is configured, an Anthropic-only credential setup defaults to `anthropic:claude-opus-5-5`; all other cases default to `openai:gpt-6.1-sol`. A configured default must be a catalog model eligible as a default, and its effort must be supported, or resolution raises a configuration error. The model catalog is also the validation source for persisted settings and reports context-window information when the provider profile is available.

Gateway enablement is tri-state by ownership: `LANGSMITH_GATEWAY_ENABLED` decides when explicitly set; otherwise a dedicated `LANGSMITH_GATEWAY_API_KEY` enables it. An instance or workspace `gateway_enabled=true` or `false` overrides that deployment default, while `null` inherits it. The gateway authenticates using the dedicated key when present, otherwise `LANGSMITH_API_KEY`; its host and OpenAI Responses behavior are separately configurable. OpenAI, Anthropic, Baseten, Fireworks, and Google GenAI have gateway routes. An unroutable provider or absent LangSmith key logs a warning and uses the direct provider rather than failing a run.

## Sandbox provider and resource configuration

`SANDBOX_TYPE` selects the sandbox implementation and defaults to `langsmith`. The lazy registry supports `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local`; an unknown value raises `ValueError` naming supported choices. Third-party Daytona, Modal, Runloop, and E2B SDKs are optional dependency groups. Selecting one causes startup to import its factory so a missing extra fails at boot with the relevant `uv sync --extra sandbox-...` instruction rather than during the first run. `local` is for development: it executes on the host and is not an isolation boundary.

The LangSmith provider alone accepts a new-sandbox snapshot, resources, and create parameters through the common factory. Its deployment resource defaults are 128 GiB filesystem, 4 vCPUs, 16 GiB memory, 7,200 seconds idle TTL, and 2,592,000 seconds delete-after-stop TTL; zero disables either TTL. At startup it validates configured numeric fields, rejects negative TTLs, and requires `SANDBOX_CREATE_EXTRA_JSON` to be a JSON object. Workspace resource values and validated workspace create parameters are more specific than these deployment values when a LangSmith sandbox is created.

Provider validation is part of the FastAPI lifespan. LangSmith configuration is validated there, and optional-provider extras are eagerly checked there; an unsupported provider name is detected when a sandbox factory is resolved. For LangSmith provisioning and workspace snapshot capture, use the deployment `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`; legacy `SANDBOX_LANGSMITH_*` overrides are not used.

## Operational checks and focused tests

When changing configuration or persistence behavior:

1. Declare a new process setting in `openswe/config.py`, including aliases/deprecation and secret classification, and consume it through `ENV`.
2. Keep credentials in deployment configuration or encrypted credential storage. Do not use workspace scripts or `create_params` as a secret channel.
3. Test startup against the selected sandbox provider and a real PostgreSQL URI; migrations are a serving prerequisite.
4. Test both workspace-routing failure modes: ordinary resolution falls back to `default`, while unreadable GitHub ownership must surface as a retryable failure.
5. Test tiered settings with an instance value, a workspace override, and a cleared override; verify fallback behavior when the Store fails.
6. Test snapshot refresh while a previous snapshot is ready, so the existing immutable ID remains usable during capture.

The repository’s focused tests cover registry alias and typed-value semantics, read-only PostgreSQL transactions, workspace-settings inheritance, and GitHub routing’s ignore and 503-retry behavior.

## See also

- [Deployment](deployment.md)
- [Authentication and security](../concepts/auth-and-security.md)
- [Models, profiles, and instructions](../concepts/models-profiles-instructions.md)
- [Sandbox providers](../integrations/sandbox-providers.md)
