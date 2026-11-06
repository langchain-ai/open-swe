---
type: "Reference"
title: "Local development and deployment"
openwiki_generated: true
verified:
  - by: openwiki/0.4.2
    at: 2026-11-04T08:09:12.042Z
sources:
  - id: openwiki-source-e201e686a785f09b6d899f0b
    resource: repo://compose.yaml
  - id: openwiki-source-bb1ebe868e35e9e500714501
    resource: repo://Dockerfile
  - id: openwiki-source-19973c87ca458faa5d03fecc
    resource: repo://docs/DEVELOPMENT.md
  - id: openwiki-source-bb241754e70259fd67d23952
    resource: repo://docs/INSTALLATION.md
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
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-035276d8c595782faca6e595
    resource: repo://openswe/api/health.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-8ad17ae4f22f510aa742fcdb
    resource: repo://openswe/slack/http.py
  - id: openwiki-source-33b1621aff91e24fa4b85e3f
    resource: repo://openswe/utils/dashboard_ui.py
  - id: openwiki-source-4cc74089c0207ec1e5a6ca3b
    resource: repo://openswe/utils/model.py
generated: { by: "openwiki/0.4.2", at: "2026-11-04T08:09:12.042Z" }
---


Open SWE is not a single local process. The normal development topology has a LangGraph-backed Open SWE backend on port 2024, a Managed Deep Agents (MDA) runtime on port 2025 where agent, reviewer, and review-scout loops execute, a PostgreSQL database for Open SWE application data, and either a built dashboard or a Vite development server. GitHub and Slack may reach only the signature-checked webhook paths through a deliberately filtered HTTPS tunnel.

## Topology and request boundary

```mermaid
flowchart LR
    Browser["Browser on localhost:2024"] --> Backend["Open SWE backend and LangGraph dev"]
    Vite["Vite on localhost:3000"] --> Backend
    GitHub["GitHub"] --> Tunnel["Filtered HTTPS tunnel"]
    Slack["Slack"] --> Tunnel
    Tunnel --> Backend
    Backend --> Postgres["PostgreSQL on localhost:5433"]
    Backend --> MDA["MDA runtime on localhost:2025"]
    MDA --> Backend
```

The local development traffic boundary: browsers remain on the backend origin; GitHub and Slack reach webhook paths through the tunnel; the backend and MDA exchange run work and authenticated callback requests.

`make dev` runs `langgraph dev --port 2024` and automatically starts the Compose PostgreSQL service unless `POSTGRES_URI` is already configured. It serves the declared LangGraph graphs, the custom FastAPI app, and—when present—the dashboard build. `make dev-mda` builds the vendored `mda` CLI if required and runs the independent MDA project on port 2025. The backend routes every new agent, reviewer, and review-scout thread to that runtime; MDA receives a signed run token and calls the backend's typed agent API for the run catalog, tools, and reporting.

The FastAPI application composes dashboard, webhook, channel, health, agent-API, and other custom routes, then mounts the dashboard last. Its thread-router middleware is deliberately ahead of the core `/threads` and `/runs` routes. The dashboard catch-all declines reserved API and Agent Server prefixes, so the UI cannot shadow them.

### Do not publish the local Agent Server

**Webhook tunnels must restrict public traffic rather than publishing the unauthenticated local Agent Server surface.** In `langgraph dev`, the Agent Server endpoints such as `/threads`, `/runs`, `/assistants`, and `/store` have no authentication. The dashboard uses its session cookie and webhooks verify signatures, but that does not protect those Agent Server endpoints if port 2024 is publicly forwarded.

Use a fixed ngrok domain and:

```bash
make tunnel NGROK_DOMAIN=<name>.ngrok-free.dev
```

The target forwards port 2024 with `examples/ngrok/webhooks-only.yml`, which returns 404 unless the request path begins `/webhooks/`. Any alternate tunnel or proxy must enforce an equivalent allowlist. Keep the dashboard, `/ok`, `/health`, and Agent Server routes at `http://localhost:2024`. A Slack OAuth setup may additionally need a narrowly scoped callback relay that redirects the public Slack callback to localhost while preserving the query string; it must not expose dashboard routes.

## Local setup and process lifecycle

Prerequisites are Python 3.14+, `uv`, Node 26+ and `pnpm` for the dashboard, Docker for the local database, and Rust/Cargo because the MDA CLI is built from the vendored SDK. Install the backend environment with `uv sync --all-extras`, then create root `.env` and `mda/.env` as ignored local secrets files.

At minimum, configure the backend with a model credential (or gateway credential), GitHub App values and webhook secret, an allowlist (`ALLOWED_GITHUB_USERS` or `ALLOWED_GITHUB_ORGS`), all five Slack values (`SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APP_ID`, `SLACK_BOT_USER_ID`, and `SLACK_BOT_USERNAME`), `TOKEN_ENCRYPTION_KEY`, `DASHBOARD_JWT_SECRET`, `MDA_RUNTIME_URL=http://localhost:2025`, and `REMOTE_RUNTIME_TOKEN_SECRET`. Set `SLACK_PUBLIC_BASE_URL` to the fixed tunnel origin when Slack OAuth/webhooks use it, but leave `LANGGRAPH_URL`, `DASHBOARD_BASE_URL`, and `DASHBOARD_API_BASE_URL` local for the usual single-origin setup.

The MDA runtime has its own environment. For local use, `mda/.env` needs `OPEN_SWE_BACKEND_URL=http://localhost:2024`, `LANGSMITH_API_KEY`, and the model key or gateway configuration it needs. MDA is intentionally an independent project: it does not import the backend and has a separately locked environment. It requires Python 3.14 or newer.

Start a built dashboard and backend, then MDA, in separate terminals:

```bash
make build-dashboard
make dev
make dev-mda
```

`make build-dashboard` emits the static client build into `ui/.output/public`. For UI changes, use `make dev-ui` instead: it starts Vite on port 3000 and the backend with `DASHBOARD_DEV_SERVER_URL=http://localhost:3000`. Requests remain at port 2024, where the backend reverse-proxies non-reserved UI paths to Vite; API routes stay on the backend and Vite's HMR WebSocket connects directly to port 3000. Restart the backend after changing `.env`, since code reload does not reload environment configuration.

`make run` is intentionally narrower: it starts only `openswe.webapp:app` under Uvicorn on port 8000. It is useful for custom FastAPI/OpenAPI inspection, but it does not host the LangGraph runtime and cannot create runs. `make swagger` regenerates the checked-in custom FastAPI schema; runtime endpoints are not included in that schema.

### Local persistence and worktrees

Compose binds PostgreSQL 16 as `127.0.0.1:5433:5432` and stores it in the named `open-swe-postgres` volume. The local default application URI is `postgresql://postgres:postgres@127.0.0.1:5433/postgres`; configure `POSTGRES_URI` to use another database instead. PostgreSQL backs migrations, users, workspaces, integrations, and the application’s transcript/analytics data. It is separate from `langgraph dev` state, whose threads, checkpoints, and Store are local to `.langgraph_api` and in-memory services rather than the application database.

When switching worktrees, stop the backend cleanly before copying or linking `.langgraph_api`. Copying gives a snapshot that diverges; symlinking permits one continuous instance but only one backend may use the shared state directory at a time. Do not run separate copied state directories against the same live integrations unless duplicate background activity is acceptable.

## Startup gates and readiness

Startup is more than “the HTTP port opened.” During lifespan initialization the backend validates the login allowlist, sandbox configuration, local model key for an explicitly localhost dashboard URL, MDA runtime URL/token, and required Slack configuration; seals local writes; requires and migrates the application database; synchronizes configured admins; starts background workers/listeners; and attempts an agent-build warmup. Missing required configuration prevents startup. MDA URLs must be absolute, and a non-loopback MDA URL must use HTTPS.

Some auxiliary failures are intentionally degraded rather than fatal. Failure to initialize analytics, transcript cross-process listening, sandbox bridge listening, UI invalidations, or agent warming is logged; the application continues, but the corresponding cross-replica freshness, reporting, or cold-start behavior is reduced. In particular, an analytics-ready deployment is not established merely by a healthy homepage.

Use these checks in order:

1. Confirm the backend on `http://localhost:2024/ok` and `GET /health`; the custom `/health` response is `{"status":"healthy"}`.
2. Confirm the dashboard itself loads. `/ok` does not prove a dashboard build exists or Vite is reachable.
3. Confirm MDA is running at port 2025 and start a test task; every new Open SWE thread requires it.
4. Confirm the public tunnel with a webhook path such as `https://<name>.ngrok-free.dev/webhooks/slack`; a non-webhook path should be rejected by the tunnel policy.
5. Test GitHub OAuth and, if configured, Slack verification. GitHub delivery history and ngrok’s local inspector (`http://localhost:4040`) distinguish an external delivery failure from backend handling.

## Build and deployment artifacts

`langgraph.json` is the LangGraph deployment manifest: it names the Python version, the three backend graphs, `openswe.webapp:app`, Agent Server auth, `.env`, and checkpointer TTL (default 43,200 seconds, swept hourly). Its Docker build instructions build the dashboard, copy `ui/.output/public` to `/opt/open-swe-dashboard`, stamp backend and UI build identity sidecars from `SOURCE_COMMIT`, and set `DASHBOARD_STATIC_DIR`. Dashboard build failure is best-effort in this deployment path: the backend may deploy without a bundled UI.

The root `Dockerfile` is a production Agent Server image. It installs Open SWE into `langchain/langgraph-api`, configures the FastAPI app, checkpointer policy, and graph map through environment variables, strips package installers, and exposes port 8000. A standalone deployment must supply Agent Server persistence and worker dependencies (`DATABASE_URI`, `REDIS_URI`, LangSmith credentials/license) as well as Open SWE’s `POSTGRES_URI`, MDA connection, application secrets, integrations, and a public `LANGGRAPH_URL`. Avoid scale-to-zero hosting because background runs depend on workers backed by Redis and PostgreSQL.

For a standalone public deployment, do not rely on the image’s default `noop` Agent Server authentication: it leaves Agent Server APIs open. Use `LANGGRAPH_AUTH_TYPE="langsmith"` with the LangSmith auth endpoint and workspace tenant ID, or put the service behind an authentication-enforcing private gateway. The dashboard and webhook handlers retain their own session/signature controls.

Deploy MDA separately with `mda deploy` and configure its `OPEN_SWE_BACKEND_URL` to the backend’s public HTTPS origin. The backend uses `MDA_RUNTIME_URL` and a LangSmith key to call MDA; `REMOTE_RUNTIME_TOKEN_SECRET` signs the run tokens MDA presents back to `/agent-api`. MDA’s `MDA_SERVICE_SECRET`, when channel integrations are enabled, is shared with the backend to authenticate channel calls. Treat these as paired deployments: a healthy backend that cannot reach a compatible MDA runtime cannot execute new agent runs.

## Observability and failure diagnosis

The useful operational identifiers are request ID, thread ID, run ID, and the MDA deployment/project. The application adds request IDs and tracing resource names; use LangSmith traces for model/run behavior and MDA deployment logs for queue/runtime behavior. MDA logs expose a practical run sequence: `Created run`, queue dequeue/start with `run_queue_ms`, graph definition/load, backend `/agent-api/...` calls, and the first model request. Search MDA logs by run ID for an execution; search by thread ID for state/stream requests without a run ID.

| Symptom | Likely boundary | First checks |
| --- | --- | --- |
| Webhook never arrives | public ingress | Tunnel is running on the fixed domain; provider URL uses it; inspect GitHub delivery history or ngrok inspector; test a `/webhooks/` path, not `/ok`. |
| `Invalid signature` or 401 from webhook | secret/runtime reload | Verify the provider secret matches `.env`; restart backend after changing it; retry Slack Request URL verification. |
| Port 2024 or 3000 cannot start | stale local process | Use `lsof -iTCP:2024 -sTCP:LISTEN` or the equivalent for 3000; stop the prior backend/Vite process before switching worktrees. |
| `/` returns Agent Server JSON or 404 | UI artifact/proxy | Run `make build-dashboard`, or use `make dev-ui`; if proxying, verify Vite is listening on 3000. |
| Login redirect fails or session is absent | OAuth origin/cookie | Register the exact callback URL, use `http://` localhost URLs for local `SameSite=Lax` behavior, and ensure an allowlist contains the user. |
| Backend exits at startup | mandatory configuration | Read the startup error for allowlist, Slack variables, `POSTGRES_URI`, MDA URL/token, or the locally selected model key. |
| Runs are accepted but do not execute | MDA or remote authorization | Confirm `make dev-mda`, matching `MDA_RUNTIME_URL`/`OPEN_SWE_BACKEND_URL`, MDA model credentials, and `REMOTE_RUNTIME_TOKEN_SECRET`. |
| Analytics/readiness fails while app serves | database permissions/migration | Inspect startup logs and analytics readiness; ensure `POSTGRES_URI` is available to application code and its role can create and evolve the analytics schema. |

For focused validation after a change, run `make test` (or specify `TEST_FILE`), `make lint`, and `make typecheck`; run `make mda-test` and `make mda-typecheck` for MDA-only changes. Database-backed test coverage is optional locally: `TEST_ANALYTICS_POSTGRES_URI` enables test databases, requires a role with `CREATEDB`, and otherwise those tests skip. CI supplies that configuration, so a local suite without it does not validate those paths.

## Related pages

- [Architecture overview](../architecture/overview.md) — system boundaries and runtime roles.
- [Security and configuration](../concepts/security-and-configuration.md) — secrets, authorization, and configuration semantics.
- [Dashboard and channels](../integrations/dashboard-and-channels.md) — user-facing ingress and integration behavior.
- [Quickstart](../quickstart.md) — initial setup path.
- [Testing strategy](../testing/strategy.md) — test layers and expectations.
