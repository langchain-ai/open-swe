import importlib
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables.config import var_child_runnable_config

from openswe.memories.curator import WorkspaceCurator
from openswe.threads.index import IndexedThread
from openswe.tools.report_platform_issue import PlatformIssue, report_platform_issue
from openswe.webhooks.event_log import EventLog
from openswe.workspaces.store import WorkspaceStore


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["public", "private", None])
async def test_report_is_logged_but_private_details_never_reach_curator(
    monkeypatch: pytest.MonkeyPatch, visibility: str | None
) -> None:
    module = importlib.import_module("openswe.tools.report_platform_issue")
    now = datetime.now(UTC)
    thread = IndexedThread(
        thread_id="thread-1",
        runtime="mda",
        metadata={"visibility": visibility, "github_token": "secret"},
        status="busy",
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(module.THREADS, "get", AsyncMock(return_value=thread))
    monkeypatch.setattr(WorkspaceStore, "id_for_slug", AsyncMock(return_value=None))
    recorded = AsyncMock(return_value=True)
    monkeypatch.setattr(EventLog, "append", recorded)
    forwarded = AsyncMock(return_value="run-1")
    monkeypatch.setattr(WorkspaceCurator, "report_problem", forwarded)
    monkeypatch.setattr(PlatformIssue, "export", AsyncMock(return_value=True))
    token = var_child_runnable_config.set(
        {"configurable": {"thread_id": "thread-1", "workspace": "default"}}
    )
    try:
        result = await report_platform_issue("The sandbox timed out", ["sandbox", "timeout"])
    finally:
        var_child_runnable_config.reset(token)

    assert uuid.UUID(result["report_id"]).version == 7
    assert result["event_log_status"] == "recorded"
    assert result["export_status"] == "exported"
    assert result["curator_status"] == (
        "forwarded" if visibility == "public" else "private_report_withheld"
    )
    source, payload = recorded.call_args.args
    assert source == "platform_issue"
    assert payload["description"] == "The sandbox timed out"
    assert payload["thread_details"]["thread"]["metadata"]["github_token"] == "[REDACTED]"
    assert recorded.call_args.kwargs["delivery_id"] == result["report_id"]
    assert payload["thread_id"] == "thread-1"
    if visibility == "public":
        assert forwarded.call_args.kwargs == {
            "report_id": result["report_id"],
            "description": "The sandbox timed out",
            "keywords": ["sandbox", "timeout"],
            "thread_id": "thread-1",
        }
    else:
        forwarded.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_delivery_failures_are_independent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(PlatformIssue, "collect", AsyncMock())
    monkeypatch.setattr(PlatformIssue, "record", AsyncMock(side_effect=RuntimeError("database")))
    monkeypatch.setattr(PlatformIssue, "forward", AsyncMock(side_effect=RuntimeError("runtime")))
    monkeypatch.setattr(PlatformIssue, "export", AsyncMock(return_value=True))
    with caplog.at_level(logging.WARNING):
        result = await report_platform_issue("broken", ["sandbox"])
    assert result["event_log_status"] == "failed"
    assert result["curator_status"] == "failed"
    assert result["export_status"] == "exported"
    assert "Could not store platform issue report" in caplog.text
    assert "Could not forward platform issue report" in caplog.text


@pytest.mark.asyncio
async def test_report_platform_issue_requires_description() -> None:
    with pytest.raises(ValueError, match="Describe the platform issue"):
        await report_platform_issue("   ", ["sandbox"])
    with pytest.raises(ValueError, match="requires a description"):
        await WorkspaceCurator("default").report_problem(
            report_id="report-1", description="   ", keywords=[], thread_id="thread-1"
        )
