import importlib
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from agent.tools import report_platform_issue


class _Unserializable:
    """Stands in for LangGraph runtime objects, which have no JSON form."""


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def create_langsmith_thread_feedback(*args: Any, **kwargs: Any) -> bool:
        calls.append({"args": args, "kwargs": kwargs})
        return True

    monkeypatch.setattr(
        importlib.import_module("agent.tools.report_platform_issue"),
        "create_langsmith_thread_feedback",
        create_langsmith_thread_feedback,
    )
    return calls


@pytest.mark.asyncio
async def test_report_platform_issue_logs_report_and_thread_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    export: list[dict[str, Any]],
) -> None:
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
            "github_token": "secret",
        },
    }

    class Threads:
        async def get(self, thread_id: str) -> dict[str, Any]:
            assert thread_id == "thread-1"
            return thread

    module = importlib.import_module("agent.tools.report_platform_issue")
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "source": "slack",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
                "__pregel_runtime": _Unserializable(),
                "__pregel_task_id": "task-1",
            }
        },
    )
    monkeypatch.setattr(module, "langgraph_client", lambda: SimpleNamespace(threads=Threads()))

    with caplog.at_level(logging.WARNING, logger="agent.tools.report_platform_issue"):
        result = await report_platform_issue(
            problem_description="The sandbox command timed out",
            keywords=["sandbox", "timeout"],
        )

    report_id = uuid.UUID(result["report_id"])
    assert report_id.version == 7
    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert record.message == "Platform issue reported"
    assert record.platform_issue_report_id == str(report_id)
    assert record.platform_issue_description == "The sandbox command timed out"
    assert record.platform_issue_keywords == ["sandbox", "timeout"]
    assert record.platform_issue_thread_details == {
        "configurable": {
            "thread_id": "thread-1",
            "source": "slack",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        },
        "thread": {
            "thread_id": "thread-1",
            "status": "busy",
            "metadata": {
                "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
                "github_token": "[REDACTED]",
            },
        },
    }
    assert result["export_status"] == "exported"
    assert len(export) == 1
    assert export[0]["args"] == ("thread-1", "platform_issue")
    assert export[0]["kwargs"] == {
        "score": 0.0,
        "comment": "The sandbox command timed out",
        "source_info": {
            "source": "report_platform_issue_tool",
            "report_id": str(report_id),
            "keywords": ["sandbox", "timeout"],
        },
    }


@pytest.mark.asyncio
async def test_report_platform_issue_survives_undiagnosable_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    export: list[dict[str, Any]],
) -> None:
    module = importlib.import_module("agent.tools.report_platform_issue")

    def no_runtime() -> dict[str, Any]:
        raise RuntimeError("called outside of a runnable context")

    monkeypatch.setattr(module, "get_config", no_runtime)

    with caplog.at_level(logging.WARNING, logger="agent.tools.report_platform_issue"):
        result = await report_platform_issue(problem_description="broken", keywords=["sandbox"])

    assert uuid.UUID(result["report_id"]).version == 7
    record = caplog.records[-1]
    assert record.message == "Platform issue reported"
    assert record.platform_issue_thread_details == {}
    assert result["export_status"] == "logged_only"
    assert export == []


@pytest.mark.asyncio
async def test_report_platform_issue_requires_description(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="agent.tools.report_platform_issue"):
        with pytest.raises(ValueError, match="Describe the platform issue"):
            await report_platform_issue(problem_description="   ", keywords=["sandbox"])
    assert caplog.records == []
