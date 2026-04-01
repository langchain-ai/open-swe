---
name: verifying-code
description: >-
  CI checks, lint fixes, type error resolution, test failures. Use when asked
  to check code quality, fix CI failures, run linting, fix mypy errors, or
  ensure code passes all checks before commit.
user-invocable: false
---

# Code Verification

## STOP — Ask Before Acting

**NEVER start fixing lint errors, type errors, or test failures immediately.** Explain what issues you found and what you plan to fix, then wait for the developer's approval before making changes.

## Quality Checks (run in order, fix at each step)

1. `ruff check --fix <files>` — auto-fix lint issues
2. `ruff check <files>` — verify no remaining lint issues
3. `ruff format <files>` — format code
4. Fix any issues surfaced by the PostToolUse hook (`python_quality.sh` runs Black, Ruff, and mypy automatically on every file edit/write)

## Fixing Lint Errors

- Ruff auto-fix handles most: `ruff check --fix <files>`
- Manual fixes: unused imports, unreachable code, shadowed names
- Follow conventions from the **writing-python** skill

## Fixing Type Errors

- Add missing type annotations to all function signatures
- `cast()` only as last resort — prefer proper typing, explain in comment
- `# type: ignore[specific_code]` only for genuine mypy false positives
- Import `Callable`, `Awaitable` from `collections.abc`, not `typing`

## Test Debugging

- Single test: `pytest tests/test_file.py::TestClass::test_name -xvs`
- Run all: `pytest tests/ -v --tb=short`
- Mock at call site, not definition
- `AsyncMock` for async functions

## Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `<type>[optional scope]: <description>`

**Allowed types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

- Description: concise imperative mood — "add retry logic" not "added retry logic"
- **CRITICAL: Do NOT commit or push without explicit developer permission**
