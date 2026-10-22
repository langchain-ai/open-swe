"""Static definitions and per-invocation managed agent factories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from managed_deepagents.define_deep_agent import DeepAgentDefinition

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedServerRuntime

AgentFactory = Callable[
    ["ManagedServerRuntime"],
    DeepAgentDefinition | Awaitable[DeepAgentDefinition],
]
AgentEntry = DeepAgentDefinition | AgentFactory

__all__ = ["AgentEntry", "AgentFactory"]
