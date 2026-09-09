"""Mirror a run's tool progress into a Linear agent session as ephemeral activities.

The terminal ``response``/``error`` activity belongs to ``agent.completion``: it
fires from the platform's own completion webhook, so it survives an observer that
died with its worker. This stream only emits it when that webhook is not wired up.
"""

import asyncio
import logging
from time import monotonic
from typing import Any

import httpx2
from langgraph_sdk.client import LangGraphClient

from agent.dispatch import COMPLETION_WEBHOOK_URL
from agent.linear.client import LinearError, linear_client
from agent.linear.schema import ActionContent, ErrorContent
from agent.utils.tool_steps import tool_event, tool_step

logger = logging.getLogger(__name__)

# Every activity is a GraphQL mutation, so progress is coalesced rather than mirrored 1:1.
_FLUSH_INTERVAL_SECONDS = 2.0

_RUN_FAILED = (
    "The run stopped before it could finish. Send another message and I'll pick it back up."
)


class LinearActivityStream:
    def __init__(self, *, session_id: str, run_id: str) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.pending: ActionContent | None = None
        self.last_flush = 0.0
        self.disabled = False

    def consume(self, part: Any) -> None:
        event = tool_event(part)
        if event is None or event.kind != "tool-started" or not event.tool_name:
            return
        action, parameter = tool_step(event.tool_name, event.tool_input)
        self.pending = ActionContent(
            action=action, parameter=parameter or event.tool_name.replace("_", " ")
        )

    async def _emit(self, content: ActionContent | ErrorContent, *, ephemeral: bool) -> None:
        try:
            await linear_client().create_agent_activity(
                self.session_id, content, ephemeral=ephemeral
            )
        except LinearError, httpx2.HTTPError:
            logger.warning(
                "Disabling the Linear activity stream",
                extra={"linear_session_id": self.session_id, "linear_run_id": self.run_id},
                exc_info=True,
            )
            self.disabled = True

    async def flush(self, *, force: bool = False) -> None:
        """Send the latest pending step; intermediate steps are dropped by design."""
        if self.disabled or self.pending is None:
            return
        now = monotonic()
        if not force and now - self.last_flush < _FLUSH_INTERVAL_SECONDS:
            return
        pending, self.pending = self.pending, None
        self.last_flush = now
        await self._emit(pending, ephemeral=True)

    async def finish(self, status: str) -> None:
        self.pending = None
        if self.disabled or status == "success" or COMPLETION_WEBHOOK_URL:
            return
        # "interrupted" means a follow-up replaced this run, which is not a failure.
        if status == "interrupted":
            return
        await self._emit(ErrorContent(body=_RUN_FAILED), ephemeral=False)


async def stream_linear_activities(
    *,
    client: LangGraphClient,
    thread_id: str,
    run_id: str,
    session_id: str,
) -> None:
    """Mirror one run's tool lifecycle into a Linear agent session."""
    stream = LinearActivityStream(session_id=session_id, run_id=run_id)
    status = "error"
    try:
        async for part in client.runs.join_stream(thread_id, run_id):
            stream.consume(part)
            await stream.flush()
        run = await client.runs.get(thread_id, run_id)
        run_status = run.get("status") if isinstance(run, dict) else None
        status = str(run_status or "error")
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception:
        logger.warning(
            "Linear activity observer failed",
            extra={"linear_session_id": session_id, "linear_run_id": run_id},
            exc_info=True,
        )
    finally:
        try:
            await asyncio.shield(stream.finish(status))
        except Exception:
            logger.warning(
                "Linear activity cleanup failed",
                extra={"linear_session_id": session_id, "linear_run_id": run_id},
                exc_info=True,
            )
