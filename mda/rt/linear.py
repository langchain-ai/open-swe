"""Linear events this deployment's channel receives, handed to the Open SWE backend.

MDA's Linear channel verifies each delivery (Linear's signature and timestamp) and
owns the conversation: it starts the run on its thread, one per agent session, so
the answer, Stop and errors reach the Linear session. The backend decides the rest,
as for any Open SWE run, from the event's validated payload.
"""

import logging
import os
from typing import Final

import httpx2
from managed_deepagents import (
    ChannelRunOptions,
    ChannelRunResult,
    LinearChannelThread,
    LinearCommentCreateEvent,
    LinearEvent,
    LinearIssueCreateEvent,
    LinearIssueUpdateEvent,
    LinearSessionEvent,
)
from pydantic import BaseModel, JsonValue

from rt.backend import BACKEND_URL_ENV

logger = logging.getLogger(__name__)

#: Open SWE's own credential between this deployment and its backend (the backend's
#: MDA_SERVICE_SECRET), which answers only callers that present it.
SERVICE_SECRET_ENV: Final = "MDA_SERVICE_SECRET"
_SERVICE_SECRET_HEADER: Final = "x-mda-service-secret"
# Preparing a run reads the issue's images; Linear waits for no answer meanwhile.
_SECONDS: Final = 60.0

LinearIssueEvent = LinearIssueCreateEvent | LinearIssueUpdateEvent | LinearCommentCreateEvent


class _PreparedRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class _Prepared(BaseModel):
    run: _PreparedRun | None


class LinearIntake:
    """Hands this deployment's Linear events to the Open SWE backend."""

    async def start_run(
        self, event: LinearSessionEvent, thread: LinearChannelThread
    ) -> ChannelRunResult | None:
        """The run an agent session turn starts, as the backend prepared it; None declines.

        A failure raises, so the channel tells the session something went wrong.
        """
        prepared = _Prepared.model_validate(
            await self._post("/linear/channel/runs", event, thread)
        ).run
        if prepared is None:
            return None
        options: ChannelRunOptions = {"context": prepared.context, "metadata": prepared.metadata}
        return (prepared.input, options)

    async def forward(self, event: LinearIssueEvent, thread: LinearChannelThread) -> None:
        """Log an event that starts no conversation, and run the automations it triggers."""
        await self._post("/linear/channel/events", event, thread)

    async def _post(
        self, path: str, event: LinearEvent, thread: LinearChannelThread
    ) -> dict[str, JsonValue]:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        secret = os.environ.get(SERVICE_SECRET_ENV, "").strip()
        if not base or not secret:
            raise RuntimeError(f"{BACKEND_URL_ENV} and {SERVICE_SECRET_ENV} are required")
        async with httpx2.AsyncClient(timeout=_SECONDS) as client:
            response = await client.post(
                f"{base}{path}",
                headers={_SERVICE_SECRET_HEADER: secret},
                json={
                    "thread_id": thread.id,
                    "delivery_id": event.delivery_id,
                    "key": event.key,
                    "payload": dict(event.payload),
                },
            )
        if response.is_error:
            logger.error(
                "The backend refused a Linear event",
                extra={"path": path, "status": response.status_code},
            )
            response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}


LINEAR: Final = LinearIntake()
