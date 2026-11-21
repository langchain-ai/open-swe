"""Return tool errors to the model instead of ending the run."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ToolErrorMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from managed_deepagents._log import logger
from managed_deepagents._run_ending import RunEndingError


def create_tool_error_middleware() -> AgentMiddleware[Any, Any, Any]:
    """Return tool errors to the model, except the errors that must end the run."""
    return ToolErrorMiddleware(_error_text)


def _error_text(error: Exception, request: ToolCallRequest) -> str | None:
    """Return the error text for the model, or `None` to end the run."""
    if isinstance(error, RunEndingError):
        return None
    name = request.tool_call["name"]
    logger.error(
        "Tool call failed: %s (%s)",
        logger.log_identifier(name),
        logger.safe_error_label(error),
        error=error,
        attributes={"tool_name": name},
    )
    return f"Error: {name} failed: {type(error).__name__}: {error}"
