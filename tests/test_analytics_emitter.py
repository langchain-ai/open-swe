import pytest

from agent.analytics import emitter


@pytest.mark.asyncio
async def test_task_rework_is_noop_when_analytics_is_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("ANALYTICS_POSTGRES_URI", raising=False)
    monkeypatch.delenv("POSTGRES_URI", raising=False)
    monkeypatch.delenv("ANALYTICS_WORKSPACE_ID", raising=False)

    await emitter.task_rework("thread-1", source="dashboard", scope="major", reason="plan_review")
