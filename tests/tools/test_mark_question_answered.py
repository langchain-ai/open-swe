import importlib
from unittest.mock import AsyncMock

import pytest

answered = importlib.import_module("agent.tools.mark_question_answered")


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"configurable": {"thread_id": "t1"}}])
async def test_missing_run_context_cannot_qualify_feedback(monkeypatch, config):
    mark = AsyncMock()
    monkeypatch.setattr(answered, "get_config", lambda: config)
    monkeypatch.setattr(answered, "mark_answered_question", mark)
    assert (await answered.mark_question_answered())["success"] is False
    mark.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["slack", "schedule", "linear", "github", "desktop"])
async def test_non_dashboard_run_cannot_qualify_feedback(monkeypatch, source):
    mark = AsyncMock()
    monkeypatch.setattr(
        answered,
        "get_config",
        lambda: {
            "run_id": "current-run",
            "configurable": {"thread_id": "current-thread", "source": source},
        },
    )
    monkeypatch.setattr(answered, "mark_answered_question", mark)
    assert (await answered.mark_question_answered())["success"] is False
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_qualifies_only_current_dashboard_thread_and_run(monkeypatch):
    mark = AsyncMock()
    monkeypatch.setattr(
        answered,
        "get_config",
        lambda: {
            "run_id": "current-run",
            "configurable": {"thread_id": "current-thread", "source": "dashboard"},
        },
    )
    monkeypatch.setattr(answered, "mark_answered_question", mark)
    assert await answered.mark_question_answered() == {"success": True}
    mark.assert_awaited_once_with("current-thread", "current-run")
