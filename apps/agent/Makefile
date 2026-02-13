.PHONY: all format lint test tests integration_tests help run dev

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

dev:
	langgraph dev

run:
	uvicorn agent.webapp:app --reload --port 8000

install:
	uv pip install -e .

######################
# TESTING
######################

TEST_FILE ?= tests/

test tests:
	uv run pytest -vvv $(TEST_FILE)

integration_tests:
	uv run pytest -vvv tests/integration_tests/

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

######################
# HELP
######################

help:
	@echo '----'
	@echo 'dev                          - run LangGraph dev server'
	@echo 'run                          - run webhook server'
	@echo 'install                      - install dependencies'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
