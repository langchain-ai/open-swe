# Render dictionary tool schemas in code mode

**Verdict: upstream as-is.** Dictionary JSON schemas are a supported LangChain tool input.

**Changed:** `langchain_quickjs/_prompt.py`.

## What

The argument renderer accepts dictionary schemas with a properties mapping and dereferences them through the same helper as Pydantic schemas.
Top-level metadata stays outside the argument fields, and rendering leaves the original schema intact.
Pydantic schemas and the generic fallback keep their existing behavior.

## Why

Robin's backend operations and MCP tools supply dictionary schemas.
Code mode discarded those schemas, hiding argument names and required fields and causing avoidable rejected calls.

## Upstream

Accept dictionary tool schemas in the code-mode argument renderer; drop this change once upstream includes that support.
