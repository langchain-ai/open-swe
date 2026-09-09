import importlib
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from agent.tools import report_platform_issue


@pytest.mark.asyncio
async def test_report_platform_issue_logs_report_and_thread_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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

    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "source": "slack",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    module = importlib.import_module("agent.tools.report_platform_issue")
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
