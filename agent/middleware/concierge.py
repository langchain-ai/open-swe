"""After-agent middleware that delivers a concierge run's response.

In concierge mode the agent has no reply tool, so whatever it finishes with is the
message the person is waiting for. Posting it here is what makes the final
response the reply.
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent.middleware.message_content import content_to_text
from agent.middleware.trace import scrub_middleware_inputs
from agent.run_config import RunConfig
from agent.slack.dm import is_dm_session

logger = logging.getLogger(__name__)

# ModelCallLimitMiddleware's marker: notify_step_limit_reached already explains
# that stop, and the marker text itself is not an answer.
_LIMIT_MARKER = "Model call limits exceeded"


@scrub_middleware_inputs
@after_agent
async def post_concierge_reply(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Deliver the run's final assistant message to the conversation it came from."""
    del runtime
    cfg = RunConfig.from_runtime()
    slack_thread = cfg.slack_thread
    if slack_thread is None or not is_dm_session(
        slack_thread.channel_context, slack_thread.thread_ts
    ):
        return None
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage):
        return None
    message = content_to_text(last.content or "").strip()
    if not message or _LIMIT_MARKER in message:
        return None

    from agent.slack.tools.thread_reply import slack_thread_reply

    try:
        result = await slack_thread_reply(message, state=dict(state))
    except Exception:
        logger.exception("Failed to deliver the reply", extra={"agent_thread_id": cfg.thread_id})
        return None
    if not result.get("success"):
        logger.warning(
            "Reply was not delivered",
            extra={"agent_thread_id": cfg.thread_id, "reason": result.get("error")},
        )
    return None
