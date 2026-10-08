from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openswe.analytics.events import (
    EventEnvelope,
    EventName,
    RunStartedPayload,
    event_uuid,
)


def test_event_payload_rejects_unexpected_content() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunStartedPayload.model_validate(
            {"model_attribution_quality": "unavailable", "prompt": {"text": "private content"}}
        )


def test_event_requires_timezone() -> None:
    workspace = uuid4()
    occurred_at = datetime(2026, 1, 1)
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope(
            event_id=event_uuid(workspace, "open-swe", "run-1:1", EventName.RUN_STARTED),
            event_name=EventName.RUN_STARTED,
            workspace_id=workspace,
            environment="test",
            producer="open-swe",
            producer_event_id="run-1:1",
            occurred_at=occurred_at,
            recorded_at=datetime.now(UTC),
            privacy_classification="non_personal",
            payload=RunStartedPayload(model_attribution_quality="unavailable"),
        )
