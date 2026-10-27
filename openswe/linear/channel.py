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
from managed_deepagents import EndpointAnswer, EndpointEvent, EndpointThread
from pydantic import JsonValue
from typing_extensions import TypedDict

from openswe.linear.session import LinearDecline, LinearSessionTurn
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


class LinearChannelCall(TypedDict):
    """What MDA's Linear channel POSTs: the event it validated and its thread.

    MDA's ``EndpointRequest``, with its fields named here: pydantic doesn't apply a
    generic TypedDict's type arguments to a subclass, and the API schema gets a
    name of its own.
    """

    event: EndpointEvent[str, dict[str, JsonValue]]
    thread: EndpointThread


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
    call: LinearChannelCall,
    background_tasks: BackgroundTasks,
) -> EndpointAnswer | LinearDecline | None:
    """The run a Linear event starts, or ``None`` to start nothing.

    An agent session turn starts the run it prepares, or declines with what the
    channel tells the session, such as that it names no repository. Issue events
    run Linear automations.
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
    if isinstance(turn, LinearSessionTurn):
        return await turn.prepare_run(thread_id)
    if turn is not None:
        return turn
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
