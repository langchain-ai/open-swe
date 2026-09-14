import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import tool as make_tool

from agent.thread_feedback import Feedback, feedback_store
from agent.tools.submit_thread_feedback import submit_thread_feedback

tool = importlib.import_module("agent.tools.submit_thread_feedback")


@pytest.fixture
async def context(fake_store: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    export = AsyncMock(return_value=True)

    @asynccontextmanager
    async def unlocked(*args: object, **kwargs: object):
        yield

    monkeypatch.setattr(tool, "agent_thread_pr_state_lock", unlocked)
    monkeypatch.setattr(tool, "langgraph_client", lambda: None)
    monkeypatch.setattr(tool, "create_langsmith_thread_feedback", export)
    return export


def test_tool_schema_hides_runtime_context() -> None:
    schema = make_tool(submit_thread_feedback).tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"rating", "comment"}


def _runtime(route: str = "performance") -> Any:
    return SimpleNamespace(
        config={"run_id": "run-1", "configurable": {"thread_id": "thread-1"}},
        state={"model_route": route},
    )


async def test_submits_explicit_feedback_with_run_context(context: AsyncMock) -> None:
    result = await submit_thread_feedback("bad", _runtime(), "  Needed a stronger model.  ")

    assert result == {
        "status": "completed",
        "rating": "bad",
        "comment": "Needed a stronger model.",
        "export_status": "exported",
    }
    assert await feedback_store().get("thread-1") == Feedback(
        status="completed",
        event_id="tool:run-1",
        answer_run_id="run-1",
        rating="bad",
        comment="Needed a stronger model.",
    )
    context.assert_awaited_once_with(
        "thread-1",
        "rating",
        score=0.0,
        comment="Needed a stronger model.",
        source_info={
            "source": "agent_thread_feedback_tool",
            "run_id": "run-1",
            "model_route": "performance",
        },
    )


async def test_rejects_second_feedback_submission(context: AsyncMock) -> None:
    await feedback_store().put(
        "thread-1", Feedback(status="completed", rating="good", comment="Already saved")
    )

    with pytest.raises(ValueError, match="already been submitted"):
        await submit_thread_feedback("bad", _runtime(), "Replace me")

    assert await feedback_store().get("thread-1") == Feedback(
        status="completed", rating="good", comment="Already saved"
    )
    context.assert_not_awaited()
