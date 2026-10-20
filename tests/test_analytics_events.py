from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.analytics.events import (
    ContextOffloadedPayload,
    EventEnvelope,
    EventName,
    RunStartedPayload,
    ToolOutputTruncatedPayload,
    deterministic_event_id,
)


def test_event_id_is_deterministic_and_versioned() -> None:
    workspace = uuid4()
    first = deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "1")
    assert first == deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "1")
    assert first != deterministic_event_id(workspace, EventName.RUN_STARTED, "run-1", "2")


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


@pytest.mark.parametrize(
    ("event_name", "payload"),
    [
        (
            EventName.TOOL_OUTPUT_TRUNCATED,
            ToolOutputTruncatedPayload(
                tool="fetch_url",
                outcome="truncated",
                original_chars=250_000,
                kept_chars=100_000,
            ),
        ),
        (
            EventName.CONTEXT_OFFLOADED,
            ContextOffloadedPayload(trigger="automatic", status="completed", cutoff_index=42),
        ),
    ],
)
def test_runtime_events_dispatch_through_the_envelope(event_name, payload) -> None:
    workspace = uuid4()
    envelope = EventEnvelope(
        event_id=deterministic_event_id(workspace, event_name, "key", "1"),
        event_name=event_name,
        workspace_id=workspace,
        environment="test",
        producer="open-swe",
        producer_event_id="key:1",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        recorded_at=datetime.now(UTC),
        privacy_classification="non_personal",
        payload=payload,
    )
    assert isinstance(envelope.payload, type(payload))
