from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.analytics.events import (
    EventEnvelope,
    EventName,
    RunStartedPayload,
    _reject_forbidden_fields,
    deterministic_event_id,
)


def test_event_id_is_deterministic_and_versioned() -> None:
    workspace = uuid4()
    first = deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "1")
    assert first == deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "1")
    assert first != deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "2")


def test_event_rejects_forbidden_nested_fields() -> None:
    with pytest.raises(ValueError, match="forbidden analytics field"):
        _reject_forbidden_fields({"safe": [{"prompt": {"text": "secret"}}]})


def test_event_requires_timezone() -> None:
    workspace = uuid4()
    occurred_at = datetime(2026, 1, 1)
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope(
            event_id=deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "1"),
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
