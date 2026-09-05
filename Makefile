.PHONY: all format format-check lint typecheck test tests integration_tests help run dev web build-dashboard desktop install-desktop install-checkout setup

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

dev:
	uv run langgraph dev --no-browser --port 2024

web:
	pnpm run dev

# Build the dashboard into ui/.output/public; `make dev` then serves it at /.
# With a LangGraph http.mount_prefix, pass DASHBOARD_BASE_PATH=<prefix>/ so the
# build's asset URLs and router match where the server mounts it.
build-dashboard:
	pnpm install --frozen-lockfile --filter open-swe-dashboard...
	pnpm --filter open-swe-dashboard run build

run:
	uv run uvicorn agent.webapp:app --reload --port 8000

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

setup:
	uv run python scripts/setup_env.py

######################
# TESTING
######################

TEST_FILE ?= tests/

test tests:
	@if [ -d "$(TEST_FILE)" ] || [ -f "$(TEST_FILE)" ]; then \
		uv run pytest -vvv $(TEST_FILE); \
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
	@echo 'dev                          - run LangGraph dev server'
	@echo 'web                          - run the dashboard web server'
	@echo 'run                          - run webhook server'
	@echo 'desktop                      - run the Electron desktop app (backend must be running)'
	@echo 'install-desktop              - install or update Open SWE Desktop on macOS'
	@echo 'install-checkout             - install the current checkout of Open SWE Desktop on macOS'
	@echo 'install                      - install dependencies (incl. dev extras)'
	@echo 'setup                        - guided .env setup for the GitHub + Slack install'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'typecheck                    - run ty on agent/ and tests/'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
	@echo '----'
