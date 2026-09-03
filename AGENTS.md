# AGENTS.md

## Project

Open SWE is a LangGraph + Deep Agents coding-agent framework. Each thread uses an isolated sandbox. A separate read-only reviewer graph reviews pull requests, and a review-style analyzer learns repository-specific review preferences.

## Commands

Dependencies are managed with `uv`. Tests use pytest, lint/format use Ruff, and type checking uses ty. Python 3.14 is required.

```bash
make install
make dev
make run
make test TEST_FILE=tests/github/test_open_pull_request.py
make format
make lint
make typecheck
```

Never run the full test suite locally; run only tests related to the change.

## Architecture

`langgraph.json` declares the graphs and FastAPI app:

| Graph | Entrypoint | Implementation |
|---|---|---|
| `agent` | `agent.graphs.agent:traced_agent` | `agent/server.py` |
| `reviewer` | `agent.graphs.reviewer:traced_reviewer_agent` | `agent/reviewer.py` |
| `analyzer` | `agent.graphs.analyzer:traced_analyzer` | `agent/analyzer.py` |
| `chat` | `agent.graphs.chat:traced_chat_agent` | `agent/chat.py` |
| `scheduler` | `agent.graphs.scheduler:get_scheduler` | `agent/scheduler.py` |

The FastAPI app is `agent.webapp:app`; dashboard routes live in `agent/dashboard/`.

Sandbox creation and reconnection live in `agent/sandboxes/`. An unreachable existing sandbox must raise rather than be replaced because replacement would lose work. Only the read-only reviewer may opt into replacement. Provider registration is in `agent/sandboxes/providers/registry.py`.

The main agent and middleware stack are assembled in `agent/server.py`. Middleware order is significant. Tools are exported from `agent/tools/__init__.py` and explicitly added to the relevant graph; do not accumulate tools without wiring and authorization review.

## Conventions

- Read relevant code and tests before editing. Fix root causes and keep diffs focused.
- Use async-only implementations. Add a sync method only when an interface requires it, and then raise `NotImplementedError`.
- Use absolute imports across packages; same-package imports may start with one dot. Never use parent-relative imports.
- Keep comments minimal and only explain non-obvious reasons.
- Use structured logging with a static message and values in `extra`; never interpolate values into log messages. Avoid standard `LogRecord` field names in `extra`.
- Add tools to `agent/tools/`, export them, and wire them into `agent/server.py` or `agent/reviewer.py`.
- Add middleware to `agent/middleware/`, export it, and place it deliberately in the stack.
- Add sandbox providers under `agent/sandboxes/providers/` and register them in `registry.py`.
- Add dashboard endpoints through `agent/dashboard/routes.py` and graph entrypoints through `langgraph.json`.
- Do not add tests that only restate static prompt text; test rendering, composition, precedence, or behavior.
- Do not hand-edit generated `openwiki/` content unless explicitly asked.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
