from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import database, emitter


@pytest.mark.asyncio
async def test_task_rework_is_noop_when_analytics_is_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URI", raising=False)

    await emitter.task_rework("thread-1", source="dashboard", scope="major", reason="plan_review")


@pytest.mark.asyncio
async def test_pr_opened_without_invocation_preserves_action(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())
    emit = AsyncMock()
    monkeypatch.setattr(emitter, "emit", emit)
    monkeypatch.setattr("agent.analytics.directory.upsert_model", AsyncMock())
    monkeypatch.setattr("agent.analytics.directory.upsert_repository", AsyncMock())

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

    opened = emit.await_args_list[0]
    assert opened.args[0] == emitter.EventName.PR_OPENED
    assert opened.args[2].opening_run_id is None
    assert opened.args[2].model_attribution_quality == "unavailable"
    assert len(emit.await_args_list) == 1
