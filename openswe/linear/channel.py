"""Routes for MDA's Linear channel.

The channel receives Linear's webhooks in the MDA deployment, verifies each
delivery's signature and age, and hands the event here: its key and the payload
it validated. The backend decides what the event does. Only the deployment, which
holds Open SWE's shared secret, may call these routes.
"""

import json
from typing import Literal, Self, TypedDict
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field, JsonValue, model_validator

from openswe.linear.session import LinearSessionTurn, PreparedRun
from openswe.mda_channels.auth import require_mda_channel
from openswe.webhooks import common
from openswe.webhooks.event_log import EventLog, EventRefs

# Linear's deliveries are a few kilobytes; anything near this is not one.
_MAX_PAYLOAD_CHARS = 1_000_000

LinearEventKey = Literal[
    "Issue.create",
    "Issue.update",
    "Comment.create",
    "AgentSessionEvent.created",
    "AgentSessionEvent.prompted",
]

router = APIRouter(prefix="/linear/channel", dependencies=[Depends(require_mda_channel)])


class LinearChannelEvent(BaseModel):
    """A Linear event the channel received and verified, on the thread it belongs to."""

    thread_id: UUID
    delivery_id: str = Field(min_length=1, max_length=200)
    key: LinearEventKey
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if len(self.body()) > _MAX_PAYLOAD_CHARS:
            raise ValueError("payload is too large")
        return self

    def body(self) -> bytes:
        """The payload as JSON, as the event log keeps deliveries."""
        return json.dumps(self.payload).encode()

    async def record(self, request: Request) -> None:
        """Log the event and wake the threads that listen for Linear events."""
        body = self.body()
        await EventLog.record(
            request,
            body,
            "linear",
            event_type=str(self.payload.get("type") or ""),
            delivery_id=self.delivery_id,
            refs=EventRefs.linear(body),
        )


class PreparedLinearRun(TypedDict):
    """The run the channel starts, or None to start nothing."""

    run: PreparedRun | None


@router.post("/runs")
async def prepare_linear_run(request: Request, event: LinearChannelEvent) -> PreparedLinearRun:
    """Prepare the run an agent session turn starts on its session's thread."""
    await event.record(request)
    turn = LinearSessionTurn.parse(event.key, event.payload)
    if turn is None:
        return {"run": None}
    return {"run": await turn.prepare_run(str(event.thread_id))}


async def _launch_automations(payload: dict[str, JsonValue], delivery_id: str) -> None:
    from openswe.schedules.store import launch_linear_automations

    try:
        await launch_linear_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Linear automations failed for a delivery", extra={"linear_delivery": delivery_id}
        )


@router.post("/events")
async def receive_linear_event(
    request: Request, event: LinearChannelEvent, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Log a Linear event that starts no conversation, and run the automations it triggers."""
    await event.record(request)
    if event.key in {"Issue.create", "Issue.update"}:
        background_tasks.add_task(_launch_automations, event.payload, event.delivery_id)
    return {"status": "accepted"}
