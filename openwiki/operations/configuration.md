---
type: operations reference
title: Configuration, secrets, and startup validation
description: Operator reference for Open SWE's centralized environment registry, required backend and Managed Deep Agents configuration, persistence, credentials, sandbox providers, and boot-time failure behavior.
tags: [configuration, operations, secrets, startup-validation, database, sandbox, managed-deep-agents]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-23T08:13:55.386Z
sources:
  - id: openwiki-source-8010c6e64af5a375d8d3b70b
    resource: repo://docs/CUSTOMIZATION.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-a13697e04823548408653de5
    resource: repo://openswe/database/postgres.py
  - id: openwiki-source-b11ec0af4e40439361058935
    resource: repo://openswe/encryption.py
  - id: openwiki-source-9bd3a8003cde29c1b2ffa556
    resource: repo://openswe/remote_runtime/client.py
  - id: openwiki-source-8813511c8e2188e838ca5502
    resource: repo://openswe/remote_runtime/tokens.py
  - id: openwiki-source-e2d4ab22e08f00169bb3c374
    resource: repo://openswe/sandboxes/providers/langsmith_api.py
  - id: openwiki-source-d16a45e9fc6aa80a3708c88c
    resource: repo://openswe/sandboxes/providers/langsmith.py
  - id: openwiki-source-a4c632cb1c0a9a7a637ab9fe
    resource: repo://openswe/sandboxes/providers/registry.py
generated: { by: "openwiki/0.4.2", at: "2026-10-23T08:13:55.386Z" }
---

# Configuration, secrets, and startup validation

Open SWE has two deployment surfaces which must be configured separately:

- The **backend** is the LangGraph/FastAPI deployment declared by `langgraph.json`. It owns the dashboard, webhook intake, PostgreSQL-backed application state, sandbox provisioning, and dispatch.
- **Managed Deep Agents (MDA)** is a separate deployment of `mda/` where every Open SWE graph actually executes. The backend calls it through `MDA_RUNTIME_URL`; MDA calls backend tool and hook endpoints with a signed run token.

Environment variables are deployment configuration: use the deployment secret manager or a protected `.env` for local development. Do not put real values in source, documentation, or dashboard settings. The definitive variable catalog, descriptions, defaults, aliases, and secret markings is [`openswe/config.py`](../../openswe/config.py).

## Configuration model

`ENV` is the sole application registry. Values are read lazily, so process code sees late secret injection, key rotation, and test overrides rather than an import-time copy. Blank or whitespace-only values are unset. A canonical variable wins over aliases; declared accessors parse comma-separated lists, integers, and conventional boolean values. Accessing an undeclared name raises, making a new setting an explicit registry change.

`langgraph.json` loads `.env`, mounts `openswe.webapp:app`, and registers the `agent`, `reviewer`, and `review-scout` graphs. Its checkpointer uses delete-based TTL cleanup with a 60-minute sweep and a 43,200-minute default TTL (30 days). Those are runtime/checkpoint settings, not a replacement for the application database described below.

## Required startup dependencies

The FastAPI lifespan is the operational gate. It pins the event loop, then performs the checks and setup below before serving. Failure in a mandatory step prevents startup. Some auxiliary subsystems log a warning and continue, deliberately preserving dashboard and run availability.

```mermaid
flowchart TD
    Boot["FastAPI lifespan"] --> Gate["Validate login gate and sandbox"]
    Gate --> MDA["Validate MDA URL and run token secret"]
    MDA --> DB["Require PostgreSQL and run migrations"]
    DB --> Optional["Start analytics and listeners"]
    Optional --> Serve["Serve application"]
    Gate --> Abort["Raise and abort startup"]
    MDA --> Abort
    DB --> Abort
    Serve --> Shutdown["Stop workers and close HTTP and database pools"]
```

The diagram shows the hard startup gates and best-effort services in the FastAPI lifespan.

Hard gates include:

- At least one of `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` is required. This prevents an unintentionally open dashboard login policy.
- The active sandbox is validated. For `langsmith`, malformed numeric capacity/TTL fields, negative TTLs, or invalid/non-object `SANDBOX_CREATE_EXTRA_JSON` raise at boot. Selecting an optional provider without its dependency extra also fails at boot.
- `MDA_RUNTIME_URL` is required and must be an absolute URL; non-localhost `http` is rejected, so a remote runtime must use HTTPS. `REMOTE_RUNTIME_TOKEN_SECRET` is also required.
- `POSTGRES_URI` is required outside the local-development default. Startup migrates the `open_swe` schema under a PostgreSQL advisory transaction lock, so concurrent replicas serialize migration work.

Analytics reporting, transcript and bridge listeners, and the UI invalidation hub are started after migration but failures there are logged rather than fatal. The job worker is started, while agent warming is best effort. Shutdown stops those services and disposes the HTTP and database pools.

### Backend database configuration

`POSTGRES_URI` is Open SWE's application database connection, distinct from any Agent Server `DATABASE_URI`. It accepts `postgres://`, `postgresql://`, or `postgresql+asyncpg://`; the first two are normalized to asyncpg. `sslmode` is translated to asyncpg's `ssl` query option, but specifying both is rejected. The database role must be able to create/manage the `open_swe` schema and migrations.

The backend creates an async SQLAlchemy pool using `ANALYTICS_POOL_SIZE` (20), `ANALYTICS_POOL_OVERFLOW` (20), and `ANALYTICS_POOL_TIMEOUT_SECONDS` (5). `POSTGRES_SLOW_QUERY_MS` defaults to 1000 and logs slower queries; set it to `0` to disable that logging. In the `local_dev` API variant only, an unset URI defaults to `postgresql://postgres:postgres@127.0.0.1:5433/postgres`.

## MDA runtime and callback trust

`MDA_RUNTIME_URL` identifies the separately deployed MDA service. `MDA_RUNTIME_API_KEY` authenticates backend-to-MDA calls and falls back to `LANGSMITH_API_KEY`. MDA hosts the backend's `agent`, `reviewer`, and `review-scout` graph choices; a requested graph outside that set is rejected before dispatch.

For each remote run, the backend signs a JWT carrying the thread ID, assistant ID, configurable data, and metadata. `REMOTE_RUNTIME_TOKEN_SECRET` supports a comma-separated ordered key list: signing uses the first key and verification tries each, allowing rotation. Tokens require the expected issuer, audience, subject, issue/expiry claims, use HS256, have a 48-hour lifetime, and permit 60 seconds of clock leeway. MDA must return this bearer token to backend hooks; backend tools use verified claims rather than model-controlled arguments for run identity.

## Dashboard URLs, browser security, and integration secrets

`LANGGRAPH_URL` is the deployment URL and is the default for `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` when the backend serves the dashboard. `DASHBOARD_STATIC_DIR` selects a built UI; `DASHBOARD_DEV_SERVER_URL` is the local Vite proxy target. `DASHBOARD_ALLOWED_ORIGINS` is a comma-separated list of additional browser origins. `create_app` rejects `*`, because CORS allows credentials, and always adds `open-swe://app` for the desktop client. Dashboard redirect handling normalizes origins and allows only `DASHBOARD_BASE_URL` plus this explicit allowlist.

`DASHBOARD_JWT_SECRET` signs dashboard session cookies and OAuth state. `TOKEN_ENCRYPTION_KEY` protects stored OAuth and MCP credentials. It accepts one Fernet key or a newest-first comma/newline list: new ciphertext uses the first key and reads try all keys. Rotate by prepending a new valid key, retaining previous keys until old ciphertext is retired or re-encrypted. Missing encryption configuration prevents encryption; a decrypt failure is logged and yields no plaintext.

Keep third-party credentials as backend deployment secrets. Common integration settings include GitHub App identity and webhook signing (`GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, and optional installation ID), Slack bot/signing and OAuth values, `LINEAR_WEBHOOK_SECRET`, and LangSmith credentials. `CONFIGURED_ADMINS` is a separate comma-separated list of GitHub logins/emails that grants administrative access; it is not the dashboard login allowlist.

## Models and provider credentials

Use a provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `FIREWORKS_API_KEY`, or `BASETEN_API_KEY`) or configure the LangSmith Gateway. `LLM_MODEL_ID`, `LLM_REASONING_EFFORT`, and `LLM_FALLBACK_MODEL_ID` select deployment defaults; `OPENAI_BASE_URL` changes the OpenAI-compatible endpoint. `EXA_API_KEY` enables web search.

Gateway routing is deployment-controlled by `LANGSMITH_GATEWAY_ENABLED`; when unset, a configured `LANGSMITH_GATEWAY_API_KEY` enables it. `LANGSMITH_GATEWAY_BASE_URL` defaults to `https://gateway.smith.langchain.com`, and `LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES` defaults to true. The gateway key is preferred for gateway calls, with `LANGSMITH_API_KEY` as fallback. Workspace settings can override the deployment gateway choice; do not confuse that persisted policy with provider secrets.

## Sandbox provider configuration

`SANDBOX_TYPE` defaults to `langsmith`; valid built-ins are `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, and `local`. The registry lazily loads the provider factory and rejects unknown names with the supported list. `daytona`, `modal`, `runloop`, and `e2b` are optional dependency groups: install `sandbox-<provider>` or `sandbox-providers` before selecting one. `local` executes on the host and is for trusted local development, not isolation.

For LangSmith sandboxes, use the backend deployment's `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`; the historical `SANDBOX_LANGSMITH_*` overrides are not used. Optional resource settings default to 128 GiB filesystem (`DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES`), 4 vCPUs (`DEFAULT_SANDBOX_VCPUS`), 16 GiB memory (`DEFAULT_SANDBOX_MEM_BYTES`), a 7200-second idle TTL, and a 2,592,000-second delete-after-stop TTL. A TTL of `0` disables that expiration. `SANDBOX_CREATE_EXTRA_JSON` is an object merged into LangSmith create requests.

`WORKSPACE_SNAPSHOT_PREFIX` defaults to `openswe` (and recognizes the deprecated `ENVIRONMENT_SNAPSHOT_PREFIX` alias). Workspace snapshots, rather than a global `DEFAULT_SANDBOX_SNAPSHOT_ID`, are the current baseline mechanism. `OPENSWE_SCRIPT_ROOT` defaults to `/open-swe/environment`; local providers may need a writable alternative. Provider-specific credentials/settings are `DAYTONA_API_KEY` and optional `DAYTONA_SANDBOX_SNAPSHOT`, `E2B_API_KEY` and `E2B_TEMPLATE`, `RUNLOOP_API_KEY`, and `MODAL_APP_NAME`.

## Operator checklist and focused tests

1. Configure and secure the backend values: PostgreSQL, login allowlist, GitHub/Slack secrets as needed, dashboard signing/encryption secrets, LangSmith/sandbox credentials, and model or gateway credentials.
2. Deploy MDA separately; set its public URL in `MDA_RUNTIME_URL`, give the backend an MDA API key if it differs from `LANGSMITH_API_KEY`, and configure the same ordered `REMOTE_RUNTIME_TOKEN_SECRET` rotation set on the trust boundary.
3. On local development, run the backend and MDA separately (`make dev` and `make dev-mda`); keep `MDA_RUNTIME_URL` at the local MDA address and configure `OPEN_SWE_BACKEND_URL` in MDA's own environment. This is MDA deployment configuration, not a backend replacement.
4. Restart after environment changes. Exercise boot with the selected sandbox provider and PostgreSQL role; a passing health endpoint does not substitute for observing migration and MDA validation.
5. Test registry alias/blank/type handling (`tests/utils/test_config.py`), LangSmith sandbox creation/retry/extra fields (`tests/sandbox/test_langsmith_sandbox_config.py`), and remote token rotation plus missing-MDA lifespan failure (`tests/reviewer/test_remote_runtime.py`).

## See also

- [Authentication and security](../concepts/auth-and-security.md)
- [Configuration and model resolution](../concepts/configuration-and-model-resolution.md)
- [Sandbox providers](../integrations/sandbox-providers.md)
- [Local development and deployment](local-development-and-deployment.md)
