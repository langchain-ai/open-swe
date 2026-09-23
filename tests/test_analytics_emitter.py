from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import emitter
from agent.database import analytics as database


@pytest.mark.asyncio
async def test_pr_opened_without_invocation_preserves_action(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    monkeypatch.setattr("agent.analytics.emitter.upsert_model", AsyncMock())
    monkeypatch.setattr("agent.analytics.emitter.upsert_repository", AsyncMock())

    await emitter.pr_opened(
        owner="langchain-ai",
        repo="open-swe",
        number=2585,
        run_key=None,
        model=None,
        source="dashboard",
        repository_private=False,
        occurred_at=datetime(2026, 9, 10, tzinfo=UTC),
    )

    [opened] = [call.args[0] for call in enqueue.await_args_list]
    assert opened.event_name == emitter.EventName.PR_OPENED
    assert opened.payload.opening_run_id is None
    assert opened.payload.model_attribution_quality == "unavailable"


@pytest.mark.asyncio
async def test_feedback_submission_uses_stable_feedback_identity(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    person_id = uuid4()

    await emitter.feedback_submitted(
        feedback_key="thread:thread-1",
        rating=5,
        source="dashboard",
        run_key="run-1",
        user_id=person_id,
    )

    event = enqueue.await_args.args[0]
    assert event.event_name == emitter.EventName.FEEDBACK_SUBMITTED
    assert event.producer_event_id == "feedback:thread:thread-1"
    assert event.payload.rating == 5
    assert event.payload.sentiment == "positive"
    assert event.user_id == person_id
    assert event.entry_point == emitter.EntryPoint.DASHBOARD
