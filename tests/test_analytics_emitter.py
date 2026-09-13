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
