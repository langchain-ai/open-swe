"""After-agent middleware that notifies users when the step limit is reached."""

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..config import langgraph_client
from ..utils.source_channel import (
    in_graph_github_token,
    notify_source_channel,
    source_context_from_configurable,
)
from ..utils.user_messages import warning

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts)


@after_agent
async def notify_step_limit_reached(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Notify the user when the agent hits its step limit.

    Runs after the agent exits. Checks whether the last AI message contains
    the ``ModelCallLimitMiddleware`` marker text; if so, replies on the channel
    the run came from so the user is not left wondering what happened.
    """
    messages = state.get("messages", [])
    if not messages:
        return None

    last_msg = messages[-1]
    content = _content_to_text(getattr(last_msg, "content", "") or "")

    if _LIMIT_MARKER not in content:
        return None

    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for the step-limit notification")
        return None

    thread_id = configurable.get("thread_id")
    await notify_source_channel(
        source_context_from_configurable(configurable),
        warning(
            "Open SWE reached its maximum step limit and had to stop. "
            "The task may be incomplete. You can retry with a more focused request, "
            "or ask it to continue from where it left off."
        ),
        github_token=in_graph_github_token(config),
        agent_thread_id=thread_id if isinstance(thread_id, str) else None,
        langgraph_client_factory=langgraph_client,
    )
    return None
