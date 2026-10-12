"""Before-model middleware that hands a running thread the events it is owed."""

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.runtime import Runtime

from openswe.database import postgres
from openswe.middleware.trace import scrub_middleware_inputs
from openswe.run_config import RunConfig
from openswe.webhooks.event_matches import EventMatch

logger = logging.getLogger(__name__)


@scrub_middleware_inputs
@before_model
async def deliver_event_matches_before_model(
    state: AgentState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Append every owed event, oldest first, so none waits for the run to end."""
    thread_id = RunConfig.from_runtime().thread_id
    if not thread_id or not postgres.configured():
        return None
    try:
        owed = await EventMatch.owed(thread_id, state["messages"])
    except Exception:  # noqa: BLE001
        logger.warning(
            "Loading owed event matches failed", extra={"agent_thread_id": thread_id}, exc_info=True
        )
        return None
    if not owed:
        return None
    return {"messages": EventMatch.messages(owed)}
