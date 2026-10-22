"""Managed ``connectors/`` authoring interface for the Python ecosystem.

First-party connectors each live in their own subpackage:

- :mod:`managed_deepagents._connectors.mcp` — MCP tools

A project selects them with ``define_mcp(...)``; the compiler discovers the
result by shape (a module-level ``connector`` attribute), not by filename.
"""

from __future__ import annotations

from managed_deepagents._connectors.mcp import McpServersConfig, McpServersDefinition

__all__ = [
    "McpServersConfig",
    "McpServersDefinition",
]
