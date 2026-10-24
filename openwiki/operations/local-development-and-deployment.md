---
type: operations guide
title: Local Development, Builds, and Deployment
description: Run the Open SWE backend, PostgreSQL, dashboard, desktop client, CLI, and Managed Deep Agents locally; package the backend and dashboard for deployment; and diagnose the operational boundaries between them.
tags: [local-development, deployment, dashboard, postgres, mda, docker, desktop, cli]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-24T08:12:50.219Z
sources:
  - id: openwiki-source-ed8e23f9ef452400a454539d
    resource: repo://cli/package.json
  - id: openwiki-source-e201e686a785f09b6d899f0b
    resource: repo://compose.yaml
  - id: openwiki-source-24f77a48f966a05631988d08
    resource: repo://desktop/package.json
  - id: openwiki-source-2f66613e587b7c57d9be522e
    resource: repo://desktop/README.md
  - id: openwiki-source-bb1ebe868e35e9e500714501
    resource: repo://Dockerfile
  - id: openwiki-source-2d11873424257deb506bd9cd
    resource: repo://examples/ngrok/webhooks-only.yml
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-5cd2390b551b634ef547ebee
    resource: repo://mda/pyproject.toml
  - id: openwiki-source-e5b7abf11bb2daa504eef827
    resource: repo://mda/README.md
  - id: openwiki-source-15d75fcce83b1cece4ea13b8
    resource: repo://openswe/agent_api/client.py
  - id: openwiki-source-35238fa18658241cc3f4f643
    resource: repo://openswe/agent_api/tokens.py
  - id: openwiki-source-035276d8c595782faca6e595
    resource: repo://openswe/api/health.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-33b1621aff91e24fa4b85e3f
    resource: repo://openswe/utils/dashboard_ui.py
  - id: openwiki-source-5b54a58d1b51cd490b0e7162
    resource: repo://package.json
  - id: openwiki-source-40275cb92c3610938f16ade3
    resource: repo://pnpm-workspace.yaml
  - id: openwiki-source-440ae1e215cb02721dda855c
    resource: repo://turbo.json
  - id: openwiki-source-436f4179fe22abf615d2f7d0
    resource: repo://ui/package.json
  - id: openwiki-source-a741d432f952c0dbfb4fb35d
    resource: repo://ui/vite.config.ts
generated: { by: "openwiki/0.4.2", at: "2026-10-24T08:12:50.219Z" }
---

# Local Development, Builds, and Deployment

Open SWE is operated as **two separate runtime projects**:

1. The backend is a LangGraph deployment: the `agent`, `reviewer`, and `review-scout` graph entrypoints, `openswe.webapp:app`, dashboard API and webhooks share its origin. It also needs application PostgreSQL.
2. `mda/` is the Managed Deep Agents (MDA) project that executes the model loop and tools. It has its own Python environment, lockfile, build/deploy command, and secrets; it must not import the backend or connect to the backend database.

The backend creates and routes new agent threads to MDA. MDA calls the backend's typed `/agent-api` endpoints with a signed run token. Consequently, a backend that can serve a health endpoint is not necessarily ready to execute agent work: the MDA URL and token signing configuration are startup requirements.

```mermaid
flowchart TD
    Browser["Dashboard browser"] --> Backend["LangGraph backend :2024"]
    Vite["Vite :3000"] --> Backend
    Webhook["GitHub or Slack webhook"] --> Backend
    Backend --> Postgres["Application PostgreSQL :5433"]
    Backend --> MDA["MDA runtime :2025"]
    MDA --> API["Backend agent API"]
    Desktop["Electron desktop"] --> Backend
    CLI["oswe CLI bridge"] --> Backend
```

The local topology shows that MDA is a peer deployment reached by the backend, while dashboard, desktop, CLI, and webhooks are backend clients or entry surfaces.

## Prerequisites and environment boundaries

The backend project requires Python 3.14 or later and `uv`; `make install` runs `uv sync --extra dev`. Dashboard, desktop, CLI, and E2E workspace packages use pnpm (the repository pins `pnpm@11.22.0`); dashboard development requires Node 22.22.2 or later. The MDA project has a distinct `mda/pyproject.toml`, requires Python 3.12 or later, but MDA builds target Python 3.13. `make dev-mda` also requires Rust/Cargo because it builds the `mda` command from the vendored Managed Deep Agents SDK.

Do not merge the environments. The root environment owns `openswe`, application migrations, and backend tests. The `mda/` environment deliberately type-checks only its own source and tests: an accidental import of `openswe` or a database driver should fail there. Install or synchronize it from within `mda/` when working on the runtime; use its locked commands below.

Create two ignored environment files, with no secrets committed:

- Root `.env` is loaded by `langgraph dev`. It holds backend integration credentials, `POSTGRES_URI` when overriding the local default, and the backend-to-MDA connection settings.
- `mda/.env` is forwarded as MDA deployment secrets and is used by `mda dev` / `mda deploy`. It holds MDA's callback URL and model/tracing credentials, not backend GitHub, Slack, or PostgreSQL credentials.

### Required MDA connection settings

The root `.env` must contain both of these values before the backend starts agent graphs:

```bash
MDA_RUNTIME_URL="http://localhost:2025"
REMOTE_RUNTIME_TOKEN_SECRET="$(openssl rand -hex 32)"
```

`MDA_RUNTIME_URL` must be an absolute HTTP(S) URL; non-loopback URLs must be HTTPS. `REMOTE_RUNTIME_TOKEN_SECRET` signs the run token that carries thread, graph, configuration, and metadata to MDA. It may be a comma-separated key ring: the first key signs and every listed key verifies, permitting a staged rotation. `MDA_RUNTIME_API_KEY` is the key used to call MDA; it falls back to `LANGSMITH_API_KEY` when unset.

The corresponding local `mda/.env` needs the backend callback origin, tracing credential, and a model credential (or gateway credential):

```bash
OPEN_SWE_BACKEND_URL="http://localhost:2024"
LANGSMITH_API_KEY=""
ANTHROPIC_API_KEY=""
```

Use the same public HTTPS relationship in deployed environments: `OPEN_SWE_BACKEND_URL` belongs to MDA, while `MDA_RUNTIME_URL`, `MDA_RUNTIME_API_KEY`, and `REMOTE_RUNTIME_TOKEN_SECRET` belong to the backend. Never point a shared backend at a developer's local MDA server or reuse production MDA secrets in a local checkout.

## Start a complete local stack

For the ordinary backend and agent loop, use separate terminals:

```bash
# terminal 1: install backend dependencies once
make install

# terminal 1: build the dashboard when the backend should serve it
make build-dashboard

# terminal 1: LangGraph graphs, FastAPI app, dashboard, and local PostgreSQL
make dev

# terminal 2: Managed Deep Agents runtime
make dev-mda
```

`make dev` runs `uv run langgraph dev --no-browser --port 2024 --n-jobs-per-worker 10`. It refuses to proceed when port 2024 is already listening and, unless `POSTGRES_URI` is set in the shell or root `.env`, first starts the local PostgreSQL service. `langgraph.json` declares the three backend graphs, `openswe.webapp:app`, `.env`, and delete-based checkpoint cleanup: a 60-minute sweep with a 43,200-minute default TTL.

`make dev-mda` builds the vendored CLI if necessary and then runs `cd mda && uv run --locked mda dev --port 2025 --no-browser`. Fresh `mda dev` starts rebuild `.mda/build` and begin with empty MDA checkpoints. MDA's dev server does not accept loopback completion webhooks, so use a non-loopback backend URL for that callback when exercising completion-webhook behavior.

The backend dispatches an MDA run named `open-swe`; the run context selects `agent`, `reviewer`, or `review-scout` and includes the signed token. MDA uses that token rather than request-body configuration when calling back to the backend. This is both an operational dependency and a security boundary: do not substitute a model-controlled configuration payload for token validation.

### Local PostgreSQL

`make postgres` runs `docker compose up -d --wait postgres`. Compose uses `postgres:16`, binds container port 5432 to **loopback-only** `127.0.0.1:5433`, waits on `pg_isready`, and retains data in the named `open-swe-postgres` volume. Thus `docker compose down` stops the service without deleting application data; remove the volume deliberately only when a clean database is wanted.

The local default is `postgresql://postgres:postgres@127.0.0.1:5433/postgres`. Set `POSTGRES_URI` to a different database only when its role can create and migrate the application schema. This is application/analytics storage; local `langgraph dev` keeps LangGraph thread/checkpoint/store state separately under `.langgraph_api` in the working directory. When changing worktrees, stop the previous backend cleanly and copy or link that ignored state only if continuity is intentional—two backend processes must not share it concurrently.

### Dashboard development modes

A production-style local server needs a static dashboard build:

```bash
make build-dashboard
make dev
```

`make build-dashboard` installs the filtered dashboard workspace with the frozen lockfile and writes the build to `ui/.output/public`. The backend serves the dashboard at `/` when the `_shell.html` build exists, while preserving `/dashboard/api`, `/webhooks`, `/health`, and LangGraph routes such as `/threads` and `/runs`. Hashed assets are immutable-cacheable.

For UI work, use the same backend origin with Vite hot reload instead:

```bash
make dev-ui
```

This runs Vite on port 3000 and `make dev` concurrently with `DASHBOARD_DEV_SERVER_URL=http://localhost:3000`. Open `http://localhost:2024`: the backend reverse-proxies dashboard HTTP content, while dashboard API and LangGraph endpoints remain on the backend. Vite's HMR WebSocket connects directly to port 3000, which is why its configuration has a strict port and explicit HMR client port. Stop a stale Vite process rather than accepting another port.

To open Vite directly, run `pnpm install` then `make web`. It proxies backend paths to `DASHBOARD_API_URL`, defaulting to `http://localhost:2024`. Direct browser use changes the cookie origin: configure `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` as `http://localhost:3000` and register that GitHub OAuth callback if needed. Do not use `*` in `DASHBOARD_ALLOWED_ORIGINS`: the backend enables credentialed CORS and rejects a wildcard.

`pnpm run dev:prod` is a distinct, high-impact mode for UI work against a deployed backend. It reads only `DASHBOARD_API_URL` from root `.env`, completes the desktop-style PKCE handoff, caches a local development session, and proxies as the deployment origin so CSRF checks work. It acts on real deployment data; use a preview deployment, not production.

### Desktop and CLI

The Electron desktop client is experimental and needs a running compatible backend; it does not start one. During local development it defaults to `http://localhost:2024`:

```bash
pnpm install
make dev
make desktop
```

`make desktop` is `pnpm run dev:desktop`. Source launches use an isolated **Open SWE Development** profile, so they do not share configuration/session state or the single-instance lock with an installed app. Override the destination with `--backend-url` or `OPEN_SWE_BACKEND_URL`; resolution is command line, environment, saved configuration, then the local default. The selected backend's GitHub App must accept `<backend-url>/dashboard/api/auth/callback`.

For packaging, `pnpm --dir desktop run pack` creates an unpacked current-platform app and `pnpm --dir desktop run dist` builds an installer in `desktop/dist/`. Both build the dashboard, package `ui/.output/public`, and embed the compiled `oswe` CLI. Packaged apps ask for an organization backend URL instead of defaulting to a maintainer deployment.

The standalone CLI is built rather than run from source:

```bash
make cli
```

This requires Bun, installs the CLI and root workspace dependencies with the frozen lockfile, and emits `cli/dist/oswe`. Bun's compile output includes its runtime, so the resulting binary does not require Node or Bun on the machine that runs it. The CLI and desktop share `bridge-client` to let a cloud agent operate a local checkout; they are backend clients, not replacements for the backend or MDA runtime.

## Webhooks and local access

For GitHub/Slack testing, route a static public hostname only to the backend webhook paths:

```bash
make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev
```

The target is port 2024 and the checked-in ngrok policy returns 404 for every path outside `/webhooks/`. This restriction matters because `langgraph dev` does not authenticate raw LangGraph APIs: publishing the whole backend port would expose routes such as threads, runs, assistants, and store. Keep dashboard use at localhost. After changing secrets in `.env`, restart the backend; code reload does not reload environment settings.

The custom FastAPI health route is `GET /health`, which returns `{"status":"healthy"}`. `/ok` is also reserved for LangGraph rather than the dashboard catch-all. Treat these as liveness signals, not proof that the dashboard bundle, PostgreSQL migrations, integrations, or MDA execution are available. Verify the actual operating path separately: dashboard login, a signed webhook delivery, database startup logs, and an agent run that reaches MDA.

## Build and deployment packaging

### LangGraph Platform image

`langgraph.json` is the backend deployment manifest. It declares Python 3.14, the backend graphs and FastAPI application, authentication entrypoint, `.env`, and dashboard build instructions. Its `dockerfile_lines` copy only the UI build inputs to a temporary build context, install the filtered pnpm workspace with the frozen lockfile, build with `DASHBOARD_BASE_PATH` derived from `http.mount_prefix`, and copy static output to `/opt/open-swe-dashboard`. The UI build is deliberately best effort: a failed build is logged but does not prevent backend deployment. `DASHBOARD_STATIC_DIR=/opt/open-swe-dashboard` makes a successful bundle available to the backend.

The mount prefix is a coupling invariant: when the backend serves under `http.mount_prefix`, build the UI with its matching `DASHBOARD_BASE_PATH` including the trailing slash. Otherwise router and asset URLs point at the wrong origin path.

### Standalone Docker

The root `Dockerfile` extends `langchain/langgraph-api:0.15.1-py3.14`, copies the repository into `/deps/open-swe`, installs the backend editable package against the platform constraints, and sets `LANGGRAPH_HTTP`, checkpointer TTL, and graph-server environment values. It exposes port 8000. It then removes package installers and `uv` from the final image, so image customization belongs before that hardening step rather than assuming an interactive pip/uv toolchain at runtime.

Build the dashboard before a standalone Docker build (or provide `DASHBOARD_STATIC_DIR` containing one):

```bash
make build-dashboard
docker build -t open-swe .
```

A standalone Agent Server additionally needs its own Postgres and Redis service configuration, LangSmith credentials/licensing, a public `LANGGRAPH_URL`, and an authentication decision for raw LangGraph endpoints. `LANGGRAPH_AUTH_TYPE=noop` leaves those raw routes open to anyone who can reach the port; use LangSmith auth or an authenticated network gateway. Do not use scale-to-zero hosting for this runtime because background work relies on Redis/Postgres-backed workers remaining available.

### Deploy MDA separately

Build or deploy MDA only from its own project after producing the vendored CLI:

```bash
make mda-cli
cd mda
uv run --locked --python 3.13 mda deploy --name open-swe-mda-staging --deployment-type prod
```

`make mda-cli` compiles the SDK's Rust CLI from `vendor/managed-deepagents-sdk` and copies it into the editable MDA package. It is rebuilt when vendored CLI sources change. `mda build` and `mda deploy` package that runtime and the shared `packages/runtime-api` contract; they do not package or deploy the backend.

Each backend environment must call its own MDA deployment. Configure the MDA deployment's `mda/.env` with its backend's public `OPEN_SWE_BACKEND_URL` and its gateway/model credentials. Configure the matching backend with that MDA URL, runtime API key, and remote run-token secret. A deployment's `mda deploy` replaces the deployment secrets from its project `.env`; review that file before deploying so an empty or local callback URL does not replace the remote configuration.

## Focused validation and troubleshooting

Use narrow checks while iterating; do **not** make the full test suite a local startup prerequisite.

- Backend Python quality checks: `make lint`, `make format-check`, or `make typecheck`. For a targeted test, use `make test TEST_FILE=tests/path_or_test.py`; analytics-oriented tests need their separate test database setting and may otherwise skip.
- MDA quality checks run only in its own environment: `make mda-typecheck` and `make mda-test` (optionally with `PYTEST_ARGS`).
- Dashboard workspace commands are `pnpm --filter open-swe-dashboard run typecheck` or `test`; root `pnpm run build`, `typecheck`, and `test` fan out through Turborepo. Desktop has `pnpm --dir desktop run check`; CLI has `pnpm --filter open-swe-cli run check`.

| Symptom | Check and corrective action |
|---|---|
| Backend will not start or agent runs cannot be created | Confirm root `.env` has an absolute `MDA_RUNTIME_URL` and nonempty `REMOTE_RUNTIME_TOKEN_SECRET`; use HTTPS except for localhost. Start `make dev-mda` separately and ensure `mda/.env` points back to the backend. |
| Port 2024 is occupied | Stop the old backend before switching worktrees. `make dev` reports the listener when `lsof` is available; do not run two backends against the same local LangGraph state. |
| PostgreSQL startup or data surprises | Check `docker compose ps` and the `open-swe-postgres` volume. `docker compose down` preserves it; intentionally remove the volume only for a reset. Ensure an explicit `POSTGRES_URI` is reachable and has schema privileges. |
| Dashboard is JSON, 404, or stale | Build with `make build-dashboard`, or use `make dev-ui`. For Vite, free port 3000; it is deliberately `strictPort`. Rebuild static assets after pulling UI changes. |
| Login or browser API calls fail in direct Vite mode | Keep `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` aligned with `:3000`, register its OAuth callback, and do not use wildcard credentialed CORS. |
| Webhook provider cannot deliver | Keep the public URL stable, run the allowlisted tunnel to port 2024, inspect provider delivery logs/ngrok, and restart backend after changing signing secrets. Do not expose the entire LangGraph dev port. |
| Docker deployment is healthy but UI or agent work is unavailable | A health response only proves liveness. Check dashboard build logs/static directory, application database migration/connectivity, raw-route authentication, the MDA URL/key/token secret, and MDA deployment logs. |
| MDA deploy behaves differently from local backend | Recheck the project boundary: deploy from `mda/` with its locked environment and MDA `.env`; set public remote callback URL there, then update the paired backend's MDA URL and signing configuration. |
