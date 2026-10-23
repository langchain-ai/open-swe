"""The events a running thread watches for, delivered before its next model call."""

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime

from openswe.database import postgres
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig
from openswe.webhooks.event_matches import EventMatch

logger = logging.getLogger(__name__)


class OwedEventMatches(OpenSWEMiddleware[AgentState]):
    """Hand the next model call every event the thread is owed, so none waits for the run to end."""

    async def adeliver_owed(
        self,
        state: Mapping[str, object],
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Every owed event, oldest first."""
        thread_id = RunConfig.from_runtime().thread_id
        if not thread_id or not postgres.configured():
            return None
        messages = state.get("messages")
        seen = (
            [m for m in messages if isinstance(m, BaseMessage)]
            if isinstance(messages, list)
            else []
        )
        try:
            owed = await EventMatch.owed(thread_id, seen)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Loading owed event matches failed",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )
            return None
        if not owed:
            return None
        return {"messages": EventMatch.messages(owed)}


owed_event_matches = OwedEventMatches()
