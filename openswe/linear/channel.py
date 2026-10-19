"""The endpoint MDA's Linear channel asks what each Linear event starts.

The channel receives Linear's webhooks in the MDA deployment, verifies each
delivery's signature and age, and POSTs the event it validated, with the thread it
belongs to, here (``channels.linear`` ``on_event``). The backend decides: an agent
session turn starts the run it prepares, on the session's thread, with the
run-complete webhook; issue and comment events start nothing, and are logged and
run Linear automations. Only the deployment, which holds Open SWE's shared secret,
may call it.
"""

import json
from typing import Literal, Self
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

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
_AUTOMATION_KEYS = frozenset({"Issue.create", "Issue.update"})

router = APIRouter(prefix="/linear/channel", dependencies=[Depends(require_mda_channel)])


class LinearEvent(BaseModel):
    """A Linear event the channel received and verified."""

    model_config = ConfigDict(populate_by_name=True)

    key: LinearEventKey
    delivery_id: str = Field(alias="deliveryId", min_length=1, max_length=200)
    payload: dict[str, JsonValue]


class ChannelThread(BaseModel):
    """The conversation's thread, the issue's or an agent session's."""

    id: UUID


class LinearChannelEvent(BaseModel):
    """What the channel sends its endpoint: the event, and the thread it belongs to."""

    event: LinearEvent
    thread: ChannelThread

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if len(self.body()) > _MAX_PAYLOAD_CHARS:
            raise ValueError("payload is too large")
        return self

    def body(self) -> bytes:
        """The payload as JSON, as the event log keeps deliveries."""
        return json.dumps(self.event.payload).encode()

    async def record(self, request: Request) -> None:
        """Log the event and wake the threads that listen for Linear events."""
        body = self.body()
        await EventLog.record(
            request,
            body,
            "linear",
            event_type=str(self.event.payload.get("type") or ""),
            delivery_id=self.event.delivery_id,
            refs=EventRefs.linear(body),
        )


@router.post("/events")
async def linear_channel_event(
    request: Request, call: LinearChannelEvent, background_tasks: BackgroundTasks
) -> PreparedRun | None:
    """The run a Linear event starts, or ``None`` to start nothing.

    An agent session turn starts the run it prepares; ``None`` there declines, and
    the channel tells the session. Issue events run Linear automations.
    """
    await call.record(request)
    event = call.event
    turn = LinearSessionTurn.parse(event.key, event.payload)
    if turn is not None:
        return await turn.prepare_run(str(call.thread.id))
    if event.key in _AUTOMATION_KEYS:
        background_tasks.add_task(_launch_automations, event.payload, event.delivery_id)
    return None


async def _launch_automations(payload: dict[str, JsonValue], delivery_id: str) -> None:
    from openswe.schedules.store import launch_linear_automations

    try:
        await launch_linear_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Linear automations failed for a delivery", extra={"linear_delivery": delivery_id}
        )
