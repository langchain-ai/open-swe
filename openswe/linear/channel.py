"""What MDA's Linear channel calls on the backend (``runtime_api.linear``).

The channel receives Linear's webhooks in the MDA deployment, verifies each
delivery, and decides what it starts there. It asks here only for what needs Open
SWE's data or signing key: to log each delivery and wake the threads that listen
for Linear events (``record_linear_delivery``); what Open SWE knows about a session
turn, and its signed run (``openswe.linear.session``); and a team's automations, and
the run each one it matched starts (``openswe.schedules.store``).
"""

import json

from runtime_api.linear import (
    LinearAutomation,
    LinearAutomationRules,
    LinearAutomationRulesRequest,
    LinearDelivery,
    LinearDeliveryRecorded,
    LinearSessionFacts,
    LinearSessionFactsRequest,
    LinearSessionStart,
    LinearTrigger,
    PlanLinearAutomation,
    PlanLinearSession,
    PlannedLinearAutomation,
)

from openswe.linear import session
from openswe.mda_channels.api import ChannelCall
from openswe.schedules import store
from openswe.webhooks.event_log import EventLog, EventRefs


async def record_linear_delivery(
    call: ChannelCall, delivery: LinearDelivery
) -> LinearDeliveryRecorded:
    """Log the delivery, once per delivery: MDA's channel calls again for a delivery
    Linear retries, or after an answer it didn't get."""
    body = json.dumps(delivery.payload).encode()
    await EventLog.record_once(
        call.request,
        body,
        "linear",
        event_type=str(delivery.payload.get("type") or ""),
        delivery_id=delivery.delivery_id,
        refs=EventRefs.linear(body),
    )
    return LinearDeliveryRecorded()


async def linear_session_facts(
    _call: ChannelCall, request: LinearSessionFactsRequest
) -> LinearSessionFacts:
    return await session.linear_session_facts(request)


async def plan_linear_session(_call: ChannelCall, plan: PlanLinearSession) -> LinearSessionStart:
    return await session.plan_linear_session(plan)


async def linear_automation_rules(
    _call: ChannelCall, request: LinearAutomationRulesRequest
) -> LinearAutomationRules:
    records = await store.linear_automation_rules(request.team)
    return LinearAutomationRules(
        automations=[
            LinearAutomation(
                id=str(record["id"]),
                triggers=[
                    LinearTrigger.model_validate(config)
                    for config in store.linear_trigger_configs(record, request.team)
                ],
            )
            for record in records
        ]
    )


async def plan_linear_automation(
    _call: ChannelCall, request: PlanLinearAutomation
) -> PlannedLinearAutomation:
    return PlannedLinearAutomation(run=await store.plan_linear_automation(request))
