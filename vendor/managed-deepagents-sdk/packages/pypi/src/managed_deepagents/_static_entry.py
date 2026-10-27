"""Validate fields on an exported static agent before managed compilation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from managed_deepagents.define_deep_agent import DeepAgentDefinition

_DYNAMIC_ONLY_FIELDS = ("instructions", "memory", "mcp", "sandbox", "skills", "browser")


def assert_static_agent_definition(definition: DeepAgentDefinition) -> None:
    """Reject run-selected fields on a static exported agent definition."""
    for field in _DYNAMIC_ONLY_FIELDS:
        if field in definition.config:
            raise TypeError(f"`{field}` requires a factory entrypoint")
