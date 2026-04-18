.PHONY: all format format-check lint test tests integration_tests help run dev dev-test fake-deps slack-socket test-e2e up up-test stop restart logs-dev logs-fake dump-fake-data

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

dev:
	set -a && . ./.env && set +a && \
	  uv run langgraph dev --port 2025 --no-browser

# Like `dev` but layers .env.playwright on top so the agent talks to
# fake-deps' scripted LLM proxy instead of real Anthropic. Used by `up-test`.
dev-test:
	set -a && . ./.env && . ./.env.playwright && set +a && \
	  uv run langgraph dev --port 2025 --no-browser

run:
	uvicorn agent.webapp:app --reload --port 8000

fake-deps:
	set -a && . ./.env && set +a && \
	  uv run --with fastapi --with uvicorn --with httpx --with aiosqlite \
	    uvicorn hack.fake_deps.app:app --reload --port 13765

# Socket Mode shim: connect to real Slack via websocket and forward events
# to the local agent at /webhooks/slack. Needs SLACK_APP_TOKEN (xapp-) in
# .env. Use this when developing against real Slack without a public URL.
slack-socket:
	set -a && . ./.env && set +a && \
	  uv run --with slack-sdk --with httpx python -m hack.slack_socket

HEADED ?=
PLAYWRIGHT_FLAGS = $(if $(HEADED),--headed --slowmo 250,)

# Loads .env, then layers .env.playwright on top so fake-deps' scripted LLM
# proxy (and its placeholder ANTHROPIC_API_KEY) override real-Anthropic
# settings from .env. Pass HEADED=1 to watch the browser.
test-e2e:
	set -a && . ./.env && . ./.env.playwright && set +a && \
	  uv run --with playwright --with pytest --with pytest-asyncio --with pytest-playwright \
	    python -m playwright install chromium >/dev/null 2>&1 ; \
	set -a && . ./.env && . ./.env.playwright && set +a && \
	  uv run --with playwright --with pytest --with pytest-asyncio --with pytest-playwright \
	    pytest -vvv $(PLAYWRIGHT_FLAGS) tests/e2e/

######################
# LIFECYCLE
######################

# Wait up to ~60s for both servers' health endpoints. Fails loudly if not up.
define WAIT_READY
	@echo "waiting for servers..."
	@for i in $$(seq 1 60); do \
	  curl -fs http://localhost:13765/health >/dev/null 2>&1 \
	    && curl -fs http://localhost:2025/ok >/dev/null 2>&1 \
	    && { echo "ready."; exit 0; }; \
	  sleep 1; \
	done; \
	echo "servers did not come up in 60s. see logs/openswe-*.log" >&2; \
	exit 1
endef

define PRINT_URLS
	@echo ""
	@echo "  Slack UI:   http://localhost:13765/#slack"
	@echo "  GitHub UI:  http://localhost:13765/#gh"
	@echo "  dev API:    http://localhost:2025"
	@echo ""
	@echo "  logs:       make logs-dev  |  make logs-fake"
endef

# Fire both servers in the background with logs in ./logs/. Idempotent —
# re-running kills the old pair first. Blocks until both are healthy.
up: stop
	@mkdir -p logs
	nohup $(MAKE) dev       > logs/openswe-dev.log       2>&1 &
	nohup $(MAKE) fake-deps > logs/openswe-fake-deps.log 2>&1 &
	$(WAIT_READY)
	@echo "started (real Anthropic)."
	$(PRINT_URLS)

# Same as `up` but the agent runs with .env.playwright layered in so it
# hits fake-deps' scripted LLM proxy. Required before `make test-e2e`.
up-test: stop
	@mkdir -p logs
	nohup $(MAKE) dev-test  > logs/openswe-dev.log       2>&1 &
	nohup $(MAKE) fake-deps > logs/openswe-fake-deps.log 2>&1 &
	$(WAIT_READY)
	@echo "started (test mode — fake scripted LLM)."
	$(PRINT_URLS)

stop:
	-pkill -f 'uvicorn hack\.fake_deps'      2>/dev/null || true
	-pkill -f 'langgraph dev --port 2025'    2>/dev/null || true
	@sleep 1
	@echo "stopped."

restart: stop up

logs-dev:
	tail -f logs/openswe-dev.log

logs-fake:
	tail -f logs/openswe-fake-deps.log

# Dump fake-deps SQLite state (Slack messages, GitHub repos/issues/pulls,
# webhook log, LLM fixtures) as a readable markdown report to ./logs/.
dump-fake-data:
	@mkdir -p logs
	@uv run python -m hack.fake_deps.dump > logs/fake-data.md
	@echo "wrote logs/fake-data.md"

install:
	uv pip install -e .

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

######################
# HELP
######################

help:
	@echo '----'
	@echo 'dev                          - run langgraph dev on :2025 with .env loaded'
	@echo 'run                          - run webhook server'
	@echo 'fake-deps                    - run unified Slack+GitHub+LLM mock on :13765 (loads .env)'
	@echo 'slack-socket                 - connect to real Slack via Socket Mode and forward events to local agent'
	@echo 'test-e2e                     - run Playwright end-to-end tests against running dev + fake-deps'
	@echo 'up                           - start `dev` and `fake-deps` in the background (real Anthropic)'
	@echo 'up-test                      - like `up` but agent uses fake scripted LLM (required for test-e2e)'
	@echo 'stop                         - stop anything started with `make up` / `make up-test`'
	@echo 'restart                      - stop + up'
	@echo 'logs-dev / logs-fake         - tail the backgrounded process logs'
	@echo 'dump-fake-data               - dump fake-deps SQLite state to logs/fake-data.md'
	@echo 'install                      - install dependencies'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
