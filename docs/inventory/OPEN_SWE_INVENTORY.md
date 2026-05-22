# Open SWE Static Inventory

Generated: 2026-05-21T21:10:02+02:00
Commit: faa27479c3b67ce65ba772cc6912a6b923540bfc
Scope: static source inventory before installation. No `uv sync --all-extras`, server start, Docker build, GitHub App creation, webhook wiring, push, or production deploy was performed.

## Key files read

- `pyproject.toml`, `uv.lock`, `Dockerfile`, `Makefile`, `langgraph.json`, `AGENTS.md`, `default_prompt.md`, `INSTALLATION.md`, `CUSTOMIZATION.md`, `ui/package.json`.
- Source directories: `agent/server.py`, `agent/webapp.py`, `agent/tools/`, `agent/integrations/`, `agent/middleware/`.

## Python package

- Project: `open-swe-agent` version `0.1.0`
- Requires Python: `>=3.11`
- License: `MIT`

### Runtime dependencies

- deepagents>=0.6.2
- fastapi>=0.136.1
- uvicorn>=0.46.0
- httpx>=0.28.1
- PyJWT>=2.12.1
- cryptography>=41.0.0
- langgraph-sdk>=0.3.13
- langchain>=1.2.17
- langgraph>=1.1.10
- markdownify>=1.2.2
- langchain-anthropic>=1.4.2
- langgraph-cli[inmem]>=0.4.24
- langsmith>=0.8.3
- langchain-openai>=1.2.1
- langchain-daytona>=0.0.5
- langchain-modal>=0.0.3
- langchain-runloop>=0.0.4
- exa-py>=2.12.1
- langchain-google-genai>=4.2.2

### Optional/dev dependencies

- dev: ['pytest>=9.0.3', 'pytest-asyncio>=1.3.0', 'ruff>=0.15.12', 'Pygments>=2.20.0']

## UI package

- Package: `open-swe-dashboard`
- Scripts:
- `npm run dev` -> `vite dev --port 3000`
- `npm run build` -> `vite build`
- `npm run preview` -> `vite preview`
- `npm run test` -> `vitest run`
- `npm run lint` -> `eslint`
- `npm run format` -> `prettier --write "**/*.{ts,tsx,js,jsx}"`
- `npm run typecheck` -> `tsc --noEmit`

### UI runtime dependencies

- @base-ui/react: ^1.4.1
- @fontsource-variable/inter: ^5.2.8
- @phosphor-icons/react: ^2.1.10
- @tailwindcss/vite: ^4.2.1
- @tanstack/react-devtools: ^0.10.0
- @tanstack/react-query: ^5.100.10
- @tanstack/react-query-devtools: ^5.100.10
- @tanstack/react-router: ^1.167.4
- @tanstack/react-router-devtools: ^1.166.9
- @tanstack/react-router-ssr-query: ^1.166.9
- @tanstack/react-start: ^1.166.15
- @tanstack/router-plugin: ^1.166.13
- class-variance-authority: ^0.7.1
- clsx: ^2.1.1
- nitro: latest
- react: ^19.2.4
- react-dom: ^19.2.4
- react-icons: ^5.6.0
- shadcn: ^4.7.0
- tailwind-merge: ^3.6.0
- tailwindcss: ^4.2.1
- tw-animate-css: ^1.4.0
- vite-tsconfig-paths: ^5.1.4

### UI dev dependencies

- @tanstack/devtools-vite: ^0.6.0
- @tanstack/eslint-config: ^0.4.0
- @testing-library/dom: ^10.4.1
- @testing-library/react: ^16.3.2
- @types/node: ^22.19.15
- @types/react: ^19.2.14
- @types/react-dom: ^19.2.3
- @vitejs/plugin-react: ^5.2.0
- jsdom: ^27.4.0
- prettier: ^3.8.1
- prettier-plugin-tailwindcss: ^0.7.2
- typescript: ^5.9.3
- vite: ^7.3.1
- vitest: ^3.2.4
- web-vitals: ^5.1.0

## Entry points and endpoints

- LangGraph graphs in `langgraph.json`: `agent.server:get_agent`, `agent.reviewer:get_reviewer_agent`, `agent.review_style_analyzer:get_review_style_analyzer`.
- FastAPI app: `agent.webapp:app`.
- `langgraph.json` loads `.env`; do not commit or log any `.env`.

### Web/API endpoints discovered

- POST /webhooks/linear
- GET /webhooks/linear
- POST /webhooks/slack
- GET /webhooks/slack
- GET /health
- POST /webhooks/github

## Tools

Custom tool modules discovered under `agent/tools/`:

- add_finding
- list_findings
- slack_read_thread_messages
- save_review_style
- slack_thread_reply
- linear_get_issue_comments
- linear_update_issue
- linear_comment
- fetch_url
- linear_delete_issue
- linear_list_teams
- linear_create_issue
- request_pr_review
- web_search
- http_request
- linear_get_issue
- update_finding
- publish_review

Deep Agents also contributes built-in sandbox file/shell/todo/subagent style tools through `create_deep_agent`.

## Sandbox providers

Discovered providers from `agent/integrations/` and `agent/utils/sandbox.py`:

- modal
- runloop
- daytona
- local
- langsmith

`SANDBOX_TYPE` defaults to `langsmith`; supported values include `langsmith`, `daytona`, `modal`, `runloop`, `local`. `local` has no isolation and is only acceptable for manual development.

## Middleware

- tool_error_handler
- check_message_queue
- ensure_no_empty_msg
- sandbox_circuit_breaker
- notify_step_limit
- exclude_tools
- model_fallback
- sanitize_tool_inputs
- refresh_slack_status

Notable runtime middleware in `agent/server.py`: sanitize tool inputs, model call limit, tool error handler, message queue injection, Slack status, empty message guard, step-limit notifier, sandbox circuit breaker, model fallback.

## GitHub/Slack/Linear/integration surfaces

- GitHub App installation token and optional per-user OAuth token resolution.
- GitHub webhooks for issue/PR/comment/review flows.
- Slack app mention and review request flows.
- Linear comment-triggered issue flow and Linear tools for comment/create/update/delete/list/get.
- Dashboard routes and profile/team/default repo settings.
- Reviewer agent capable of reading PR diffs and publishing findings/reviews.

## Environment variables discovered/relevant

- ALLOWED_GITHUB_ORGS
- ALLOWED_GITHUB_REPOS
- ALLOWED_REVIEWER_GITHUB_ORGS
- ALLOWED_REVIEWER_GITHUB_REPOS
- ANTHROPIC_API_KEY
- DASHBOARD_ALLOWED_ORIGINS
- DAYTONA_API_KEY
- DAYTONA_SANDBOX_SNAPSHOT
- DAYTONA_SANDBOX_SNAPSHOT_ENV
- DEFAULT_DAYTONA_SANDBOX_SNAPSHOT
- DEFAULT_LLM_MAX_TOKENS
- DEFAULT_LLM_MODEL_ID
- DEFAULT_LLM_REASONING
- DEFAULT_MODEL_ID
- DEFAULT_PROMPT_PATH
- DEFAULT_RECURSION_LIMIT
- DEFAULT_REPO_NAME
- DEFAULT_REPO_OWNER
- DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS
- DEFAULT_SANDBOX_IDLE_TTL_SECONDS
- DEFAULT_SANDBOX_MEM_BYTES
- DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES
- DEFAULT_SANDBOX_SNAPSHOT_ID
- DEFAULT_SANDBOX_VCPUS
- DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES
- EXA_API_KEY
- GH_TOKEN
- GITHUB_APP_ID
- GITHUB_APP_INSTALLATION_ID
- GITHUB_APP_PRIVATE_KEY
- GITHUB_OAUTH_PROVIDER_ID
- GITHUB_USER_EMAIL_MAP
- GITHUB_WEBHOOK_SECRET
- GOOGLE_API_KEY
- LANGCHAIN_PROJECT
- LANGCHAIN_REVISION_ID
- LANGCHAIN_TRACING_V2
- LANGGRAPH_URL
- LANGGRAPH_URL_PROD
- LANGSMITH_AGENT_VERSION
- LANGSMITH_API_KEY
- LANGSMITH_API_KEY_PROD
- LANGSMITH_ENDPOINT
- LANGSMITH_TENANT_ID_PROD
- LANGSMITH_TRACING_PROJECT_ID_PROD
- LANGSMITH_URL_PROD
- LINEAR_API_KEY
- LINEAR_TEAM_TO_REPO
- LINEAR_WEBHOOK_SECRET
- LLM
- LLM_FALLBACK_MODEL_ID
- LLM_MODEL_ID
- LOCAL_SANDBOX_ROOT_DIR
- MODAL_APP_NAME
- MODEL_CALL_RECURSION_LIMIT
- OPENAI_API_KEY
- PUBLIC_REPO_ORG_GATE
- RUNLOOP_API_KEY
- SANDBOX_BACKENDS
- SANDBOX_CREATING
- SANDBOX_CREATION_TIMEOUT
- SANDBOX_FACTORIES
- SANDBOX_POLL_INTERVAL
- SANDBOX_TYPE
- SLACK_BOT_TOKEN
- SLACK_BOT_USERNAME
- SLACK_BOT_USER_ID
- SLACK_REPO_
- SLACK_REPO_NAME
- SLACK_REPO_OWNER
- SLACK_SIGNING_SECRET
- TOKEN_ENCRYPTION_KEY

## Dockerfile / install steps

- Base image: `python:3.14.0-slim-trixie`, which conflicts with upstream installation doc saying Python 3.14 is not supported for dependency install. Treat this as YELLOW until reconciled.
- Uses Debian apt to install git, curl, wget, ca-certificates, gnupg, lsb-release, build-essential, openssh-client, jq, unzip, zip.
- Adds Docker apt key/repo from `https://download.docker.com/linux/debian/gpg`; installs pinned Docker CLI package.
- Downloads `gh` `.deb` from GitHub releases.
- Downloads `uv` tarball from GitHub releases and validates SHA256 for amd64/arm64.
- Runs `curl -fsSL https://deb.nodesource.com/setup_22.x | bash -`; this must not be run blindly.
- Downloads Go tarball from `https://go.dev/dl/...` and pipes to tar; no checksum in Dockerfile.

## Executable scripts/workflows

Scripts under `scripts/`:
- scripts/create_sandbox_snapshot.py
- scripts/list_snapshots.py
- scripts/check_pr_merge_status.py
- scripts/__init__.py

GitHub Actions workflows:
- .github/workflows/promote_main_to_prod.yml
- .github/workflows/pr_lint.yml
- .github/workflows/ci.yml

Makefile targets include: dev (`uv run langgraph dev`), run (`uv run uvicorn agent.webapp:app --reload --port 8000`), install (`uv pip install -e .`), test, integration_tests, lint, format, format-check.

## Initial Northstar implications

- Default prompt currently points to `langchain-ai/langchainplus`; must be changed for Northstar before any runtime use.
- Allowlist defaults are permissive when empty; Northstar must set `ALLOWED_GITHUB_REPOS=ollehillbom1/north-star-erp` and avoid org-wide allowlists at first.
- Slack and Linear routes/tools exist; keep disabled initially.
- HTTP/fetch/search tools are egress-capable and should be off for coding-runs unless an explicit task requires them.
