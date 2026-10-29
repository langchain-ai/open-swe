"""What MDA's Linear channel calls on the backend (``runtime_api.linear``).

The channel receives Linear's webhooks in the MDA deployment, verifies each
delivery, and decides what it starts there. It hands every delivery it handles
here to be logged, to wake the threads that listen for Linear events, and to run
the Linear automations an issue event triggers (``record_linear_delivery``). For
an agent session turn it asks what needs Open SWE's data (``start_linear_session``,
``openswe.linear.session``).
"""

import json

from pydantic import JsonValue
from runtime_api.linear import (
    LinearDelivery,
    LinearDeliveryRecorded,
    LinearSessionStart,
    LinearSessionTurn,
)

from openswe.linear.session import LinearSession
from openswe.mda_channels.api import ChannelCall
from openswe.webhooks import common
from openswe.webhooks.event_log import EventLog, EventRefs

_AUTOMATION_KEYS = frozenset({"Issue.create", "Issue.update"})


async def record_linear_delivery(
    call: ChannelCall, delivery: LinearDelivery
) -> LinearDeliveryRecorded:
    """Log the delivery and run its automations, once per delivery.

    MDA's channel calls again for a delivery Linear retries, or after an answer it
    didn't get.
    """
    body = json.dumps(delivery.payload).encode()
    await EventLog.record_once(
        call.request,
        body,
        "linear",
        event_type=str(delivery.payload.get("type") or ""),
        delivery_id=delivery.delivery_id,
        refs=EventRefs.linear(body),
    )
    if delivery.key in _AUTOMATION_KEYS:
        call.background.add_task(_launch_automations, delivery.payload, delivery.delivery_id)
    return LinearDeliveryRecorded()


async def start_linear_session(_call: ChannelCall, turn: LinearSessionTurn) -> LinearSessionStart:
    """The run a session turn starts on its thread, or why it starts none."""
    return await LinearSession(turn).start()


async def _launch_automations(payload: dict[str, JsonValue], delivery_id: str) -> None:
    from openswe.schedules.store import launch_linear_automations

    try:
        await launch_linear_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Linear automations failed for a delivery", extra={"linear_delivery": delivery_id}
        )
