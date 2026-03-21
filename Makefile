.PHONY: all format format-check lint test tests integration_tests help run dev

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

dev:
	langgraph dev

run:
	uvicorn agent.webapp:app --reload --port 8000

proxy:
	python -m agent.claude_code_proxy

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
	@echo 'dev                          - run LangGraph dev server'
	@echo 'run                          - run webhook server'
	@echo 'proxy                        - run Claude Code local API proxy (port 9999)'
	@echo 'install                      - install dependencies'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
