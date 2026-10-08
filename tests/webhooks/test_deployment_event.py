"""A recorded deployment can be rendered for a listening thread."""

from datetime import UTC, datetime
from uuid import uuid4

from openswe.webhooks.event_log import LoggedEvent
from openswe.webhooks.event_subscriptions import EventSummary


def test_a_deployment_event_can_wake_a_subscription() -> None:
    summary = EventSummary.of(
        LoggedEvent(
            source="deployment",
            event_type="deployed",
            delivery_id="delivery",
            received_at=datetime.now(UTC),
            user_id=None,
            workspace_id=uuid4(),
            repository_id=uuid4(),
            pull_request_id=None,
            payload={"target": "gcp-staging", "commits": ["c" * 40]},
        )
    )
    assert summary.from_open_swe is False
    assert summary.target == "gcp-staging"
    assert "c" * 40 not in summary.details
