"""What MDA's Linear channel calls on the backend (``runtime_api.linear``).

The channel receives Linear's webhooks in the MDA deployment, verifies each
delivery, and decides what it starts there. It asks here only for what needs Open
SWE's data or signing key: what Open SWE knows about a session turn, and its signed
run (``openswe.linear.session``); and a team's automations, and the runs the ones
it matched start (``openswe.schedules.store``). The first call for a delivery logs it
and wakes the threads that listen for Linear events; ``record_linear_delivery`` logs
one that asks nothing else.
"""

import json
from collections.abc import Mapping

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
    PlanLinearAutomations,
    PlanLinearSession,
    PlannedLinearAutomations,
)

from openswe.linear import session
from openswe.mda_channels.api import ChannelCall
from openswe.schedules import store
from openswe.users import User
from openswe.webhooks.event_log import EventLog, EventRefs


async def _record(call: ChannelCall, delivery: LinearDelivery) -> None:
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


async def record_linear_delivery(
    call: ChannelCall, delivery: LinearDelivery
) -> LinearDeliveryRecorded:
    await _record(call, delivery)
    return LinearDeliveryRecorded()


async def linear_session_facts(
    call: ChannelCall, request: LinearSessionFactsRequest
) -> LinearSessionFacts:
    await _record(call, request.delivery)
    return await session.linear_session_facts(request)


async def plan_linear_session(_call: ChannelCall, plan: PlanLinearSession) -> LinearSessionStart:
    return await session.plan_linear_session(plan)


def _creator_email(payload: Mapping[str, object]) -> str | None:
    """The email of the issue's creator, as the delivery names it."""
    data = payload.get("data")
    creator = data.get("creator") if isinstance(data, Mapping) else None
    email = creator.get("email") if isinstance(creator, Mapping) else None
    return email if isinstance(email, str) and email else None


async def linear_automation_rules(
    call: ChannelCall, request: LinearAutomationRulesRequest
) -> LinearAutomationRules:
    await _record(call, request.delivery)
    records = await store.linear_automation_rules(request.team)
    if not records:
        return LinearAutomationRules(automations=[])
    creator = _creator_email(request.delivery.payload)
    return LinearAutomationRules(
        creator_registered=bool(await User.login_for_email(creator)),
        automations=[
            LinearAutomation(
                id=str(record["id"]),
                prompt=str(record["prompt"]),
                triggers=[
                    LinearTrigger.model_validate(config)
                    for config in store.linear_trigger_configs(record, request.team)
                ],
            )
            for record in records
        ],
    )


async def plan_linear_automations(
    _call: ChannelCall, plan: PlanLinearAutomations
) -> PlannedLinearAutomations:
    """Each automation's run, in order. One that fails fails the call, so Linear sends
    the delivery again: the ones already claimed plan the same threads again."""
    return PlannedLinearAutomations(
        runs=[await store.plan_linear_automation(each) for each in plan.each()]
    )
