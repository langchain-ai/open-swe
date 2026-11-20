"""Return tool errors to the model instead of ending the run."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain.agents.middleware import ToolErrorMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from managed_deepagents._agent_auth import CredentialAuthorizationRequiredError
from managed_deepagents._channels.slack.files import SlackConnectionError
from managed_deepagents._log import logger

_ENDS_THE_RUN = (CredentialAuthorizationRequiredError, SlackConnectionError)


def create_tool_error_middleware() -> AgentMiddleware[Any, Any, Any]:
    """Return tool errors to the model, except the errors that must end the run."""
    return ToolErrorMiddleware(_error_text)


def handles_tool_errors(middleware: Iterable[object]) -> bool:
    """Whether the author's middleware already decides which tool errors end the run.

    Open SWE only: its middleware returns tool errors to the model itself, and lets the
    ones it means to end the run escape, so MDA adds none of its own there.
    """
    return any(getattr(m, "_openswe_handles_tool_errors", False) is True for m in middleware)


def _error_text(error: Exception, request: ToolCallRequest) -> str | None:
    """Return the error text for the model, or `None` to end the run."""
    if isinstance(error, _ENDS_THE_RUN):
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
