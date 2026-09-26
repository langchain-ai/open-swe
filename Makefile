.PHONY: all format format-check lint typecheck test tests integration_tests help run dev dev-ui postgres migration tunnel web build-dashboard desktop install-desktop install-checkout swagger cli

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

# Local PostgreSQL. An explicit POSTGRES_URI (shell environment, or the gitignored .env
# that langgraph dev loads) is left alone; without one, `dev` starts a postgres:16 container
# on loopback port 5433 with a named volume, so `docker rm` does not lose local data.
ifeq ($(origin POSTGRES_URI),undefined)
POSTGRES_URI := $(shell sh scripts/dotenv_value.sh .env POSTGRES_URI)
endif
dev: $(if $(POSTGRES_URI),,postgres)
	@if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:2024 -sTCP:LISTEN >/dev/null 2>&1; then \
		echo 'Port 2024 is already in use (a stale container or another backend?):' >&2; \
		lsof -nP -iTCP:2024 -sTCP:LISTEN >&2; exit 1; fi
	uv run langgraph dev --no-browser --port 2024 --n-jobs-per-worker 10

postgres:
	docker compose up -d --wait postgres

migration:
	uv run python scripts/new_migration.py "$(m)"

# UI development in one terminal: Vite (`make web`) and the backend fronting it, so
# http://localhost:2024 hot-reloads without a build or any cross-origin setup. The two
# run side by side under -j2; Ctrl-C stops both. Command-line variables reach both recipes.
dev-ui:
	$(MAKE) --no-print-directory -j2 web dev DASHBOARD_DEV_SERVER_URL=http://localhost:3000 TURBO_UI=stream

web:
	pnpm run dev

# Public URL for GitHub and Slack webhooks while developing (docs/DEVELOPMENT.md, step 3).
# ngrok's free plan includes one static domain: NGROK_DOMAIN=<name>.ngrok-free.dev. A pasted
# https:// URL with a trailing slash also works. The policy file exposes only /webhooks/*;
# langgraph dev has no auth, so the rest of the API stays local.
# Another tunnel is fine only if it enforces the same /webhooks/* allowlist (or a filtering proxy does).
tunnel:
	@test -n "$(NGROK_DOMAIN)" || { echo 'Set NGROK_DOMAIN=<your-domain>.ngrok-free.dev (claim it under Domains at https://dashboard.ngrok.com)' >&2; exit 1; }
	$(eval DOMAIN := $(shell DOMAIN="$(NGROK_DOMAIN)"; DOMAIN="$${DOMAIN#https://}"; DOMAIN="$${DOMAIN#http://}"; echo "$${DOMAIN%/}"))
	ngrok http 2024 --url https://$(DOMAIN) --traffic-policy-file examples/ngrok/webhooks-only.yml

# Build the dashboard into ui/.output/public; `make dev` then serves it at /.
# With a LangGraph http.mount_prefix, pass DASHBOARD_BASE_PATH=<prefix>/ so the
# build's asset URLs and router match where the server mounts it.
build-dashboard:
	pnpm install --frozen-lockfile --filter open-swe-dashboard...
	pnpm --filter open-swe-dashboard run build

run:
	uv run uvicorn agent.webapp:app --reload --port 8000

swagger:
	uv run python -c 'import json; from pathlib import Path; from agent.webapp import app; Path("swagger.json").write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")'

desktop:
	pnpm run dev:desktop

install-desktop:
	@test -z "$$(git status --porcelain)" || { echo 'Commit or stash repository changes first.' >&2; exit 1; }
	@git switch main
	@git pull --ff-only origin main
	@./scripts/install_desktop.sh

install-checkout:
	@./scripts/install_desktop.sh

install:
	uv sync --extra dev

# Single-file `oswe` binary. Bun compiles its own runtime into the output,
# so the result runs on any machine without Node or Bun installed.
cli:
	@command -v bun >/dev/null 2>&1 || { echo 'bun is required: https://bun.com/docs/installation' >&2; exit 1; }
	pnpm install --frozen-lockfile --filter open-swe-cli --filter open-swe
	pnpm --filter open-swe-cli run build
	@echo "Built $(CURDIR)/cli/dist/oswe"

######################
# TESTING
######################

TEST_FILE ?= tests/
PYTEST_ARGS ?=

test tests:
	@if [ -d "$(TEST_FILE)" ] || [ -f "$(TEST_FILE)" ]; then \
		uv run pytest -vvv $(PYTEST_ARGS) $(TEST_FILE); \
	else \
		echo "Skipping tests: path not found: $(TEST_FILE)"; \
	fi

integration_tests:
	@if [ -d "tests/integration_tests/" ] || [ -f "tests/integration_tests/" ]; then \
		uv run pytest -vvv tests/integration_tests/; \
	else \
		echo "Skipping integration tests: path not found: tests/integration_tests/"; \
	fi

######################
# LINTING AND FORMATTING
######################

PYTHON_FILES=.

lint:
	uv run ruff check $(PYTHON_FILES)
	uv run ruff format $(PYTHON_FILES) --diff

format:
	uv run ruff format $(PYTHON_FILES)
	uv run ruff check --fix $(PYTHON_FILES)

format-check:
	uv run ruff format $(PYTHON_FILES) --check

typecheck:
	uv run ty check agent tests

######################
# HELP
######################

help:
	@echo '----'
	@echo 'dev                          - run LangGraph dev server (starts the local PostgreSQL container unless POSTGRES_URI is set)'
	@echo 'dev-ui                       - Vite dev server plus the LangGraph dev server fronting it (UI hot reload on :2024)'
	@echo 'postgres                     - start the local PostgreSQL container on 127.0.0.1:5433'
	@echo 'migration m="..."            - create the next database migration'
	@echo 'web                          - run the dashboard web server'
	@echo 'tunnel                       - ngrok tunnel to :2024 on NGROK_DOMAIN, webhooks only (any other tunnel works too)'
	@echo 'run                          - run webhook server'
	@echo 'swagger                      - regenerate swagger.json from the backend routes'
	@echo 'desktop                      - run the Electron desktop app (backend must be running)'
	@echo 'install-desktop              - install or update Open SWE Desktop on macOS'
	@echo 'install-checkout             - install the current checkout of Open SWE Desktop on macOS'
	@echo 'install                      - install dependencies (incl. dev extras)'
	@echo 'cli                          - build the oswe CLI binary into cli/dist/oswe'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'typecheck                    - run ty on agent/ and tests/'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
	@echo '----'
