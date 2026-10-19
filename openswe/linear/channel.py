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
from typing import Any, Literal, cast, get_args
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from managed_deepagents import EndpointAnswer, EndpointRequest
from pydantic import JsonValue

from openswe.linear.session import LinearSessionTurn
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
_EVENT_KEYS: frozenset[str] = frozenset(get_args(LinearEventKey))
_AUTOMATION_KEYS = frozenset({"Issue.create", "Issue.update"})

router = APIRouter(prefix="/linear/channel", dependencies=[Depends(require_mda_channel)])


async def _record(request: Request, payload: dict[str, JsonValue], delivery_id: str) -> None:
    """Log the event and wake the threads that listen for Linear events."""
    body = json.dumps(payload).encode()
    await EventLog.record(
        request,
        body,
        "linear",
        event_type=str(payload.get("type") or ""),
        delivery_id=delivery_id,
        refs=EventRefs.linear(body),
    )


@router.post("/events")
async def linear_channel_event(
    request: Request,
    call: EndpointRequest[dict[str, JsonValue]],
    background_tasks: BackgroundTasks,
) -> EndpointAnswer | None:
    """The run a Linear event starts, or ``None`` to start nothing.

    An agent session turn starts the run it prepares; ``None`` there declines, and
    the channel tells the session. Issue events run Linear automations.
    """
    event = call["event"]
    key, payload, delivery_id = event["key"], event["payload"], event["deliveryId"]
    if key not in _EVENT_KEYS:
        raise HTTPException(status_code=422, detail="Unsupported Linear event")
    try:
        thread_id = str(UUID(call["thread"]["id"]))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid thread") from error
    if not delivery_id or len(json.dumps(payload)) > _MAX_PAYLOAD_CHARS:
        raise HTTPException(status_code=422, detail="Invalid Linear event")
    await _record(request, payload, delivery_id)
    turn = LinearSessionTurn.parse(key, cast(Any, payload))
    if turn is not None:
        return await turn.prepare_run(thread_id)
    if key in _AUTOMATION_KEYS:
        background_tasks.add_task(_launch_automations, payload, delivery_id)
    return None


async def _launch_automations(payload: dict[str, JsonValue], delivery_id: str) -> None:
    from openswe.schedules.store import launch_linear_automations

    try:
        await launch_linear_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Linear automations failed for a delivery", extra={"linear_delivery": delivery_id}
        )
