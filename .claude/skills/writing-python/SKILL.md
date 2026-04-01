---
name: writing-python
description: >-
  Python conventions for open-swe. Use when writing, editing, or reviewing Python code.
user-invocable: false
---

# Python Conventions

**Ask before making changes. Explain your plan, wait for approval.**

Python 3.11+. Ruff + Black (120 chars). mypy strict.

## Key Rules
- **Max 3 positional args** — use `*` then keyword-only for the rest
- **Reuse first** — search `agent/utils/` before writing new helpers
- **Shared code in `agent/utils/`** — if usable more than once, don't inline it in tools
- **Lean diffs** — only change what the ticket asks for
- **No hardcoded values, no `print()`, no secrets in code**
- Use `list[str]` not `List[str]`; import `Callable` from `collections.abc`
- `logger = logging.getLogger(__name__)` — include `thread_id`/`repo` context
- Tools return `{"success": bool, "error": str | None}` — never raise

## Data Structures
TypedDict for shaped dicts. Pydantic for LLM output. StrEnum for constants (values match names).

## Async
`asyncio.run()` in sync tools. `asyncio.to_thread()` in async middleware. Never `time.sleep()` in async.

## Testing
`tests/test_<module>.py`. `@pytest.mark.asyncio`. Mock at call site. `AsyncMock` for async.
