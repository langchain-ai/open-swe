"""Routes for MDA's Linear channel.

The channel receives Linear's webhooks in the MDA deployment and verifies each
delivery's signature and age. An agent session turn comes to ``/runs`` as its key
and the payload the channel validated, and the backend prepares the run the
channel starts. Issue and comment events start no conversation: the channel
forwards them to ``/events`` as Linear sent them. Only the deployment, which holds
Open SWE's shared secret, may call these routes.
"""

import json
from typing import Annotated, Literal, Self, TypedDict
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import (
    BaseModel,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    model_validator,
)

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
#: The events the channel forwards; they start no conversation.
_FORWARDED_KEYS = frozenset({"Issue.create", "Issue.update", "Comment.create"})
_AUTOMATION_KEYS = frozenset({"Issue.create", "Issue.update"})

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
        await _record(request, self.body(), self.payload, self.delivery_id)


async def _record(
    request: Request, body: bytes, payload: dict[str, JsonValue], delivery_id: str
) -> None:
    await EventLog.record(
        request,
        body,
        "linear",
        event_type=str(payload.get("type") or ""),
        delivery_id=delivery_id,
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


_payload = TypeAdapter(dict[str, JsonValue])


@router.post("/events")
async def receive_linear_event(
    request: Request,
    background_tasks: BackgroundTasks,
    linear_delivery: Annotated[str, Header(min_length=1, max_length=200)],
) -> dict[str, str]:
    """Log a Linear delivery the channel forwarded, and run the automations it triggers."""
    body = await request.body()
    if len(body) > _MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=413, detail="payload is too large")
    try:
        payload = _payload.validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="payload is not a JSON object") from error
    key = f"{payload.get('type')}.{payload.get('action')}"
    if key not in _FORWARDED_KEYS:
        return {"status": "ignored"}
    await _record(request, body, payload, linear_delivery)
    if key in _AUTOMATION_KEYS:
        background_tasks.add_task(_launch_automations, payload, linear_delivery)
    return {"status": "accepted"}
