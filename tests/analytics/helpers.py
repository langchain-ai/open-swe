"""Analytics event builders with deterministic occurrence dates."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agent.analytics.events import EventName, RunStartedPayload, make_event
from agent.database import analytics as database

DAY = datetime(2026, 9, 7, tzinfo=UTC)


def event(workspace, name, payload, *, day=0, **identifiers):
    return make_event(
        workspace_id=workspace,
        event_name=name,
        producer="test",
        producer_event_id=str(uuid4()),
        occurred_at=DAY + timedelta(days=day),
        environment="test",
        payload=payload,
        **identifiers,
    )


def run_event(*, occurred_at=None, person=None):
    return make_event(
        workspace_id=database.workspace_id(),
        event_name=EventName.RUN_STARTED,
        producer="test",
        producer_event_id=str(uuid4()),
        occurred_at=occurred_at or datetime.now(UTC),
        environment="test",
        payload=RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=uuid4(),
        user_id=person,
    )
