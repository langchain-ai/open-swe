import sys
from unittest.mock import AsyncMock

import pytest

import agent.tools.open_pull_request  # noqa: F401

opr = sys.modules["agent.tools.open_pull_request"]


@pytest.mark.asyncio
async def test_omnia_rejects_default_branch_before_creating_unreviewable_pr(monkeypatch):
    monkeypatch.setattr(opr, "get_config", lambda: {"configurable": {"source": "omnia"}})
    create = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(opr, "_open_pull_request", create)

    result = await opr.open_pull_request(
        "PetLovers-hq", "Omnia", "open-swe/chat-search", "main", "Chat search", "Search rooms"
    )

    assert result["success"] is False
    assert result["recoverable_by_agent"] is True
    assert "agent/luna/task-" in result["error"]
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,head",
    [
        ("omnia", "agent/luna/task-10-run-2744"),
        ("omnia", "agent/luna/task-10-attempt-2"),
        ("slack", "open-swe/chat-search"),
    ],
)
async def test_expected_task_branches_and_other_sources_still_publish(monkeypatch, source, head):
    monkeypatch.setattr(opr, "get_config", lambda: {"configurable": {"source": source}})
    create = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(opr, "_open_pull_request", create)
    assert (
        await opr.open_pull_request(
            "PetLovers-hq", "Omnia", head, "main", "Chat search", "Search rooms"
        )
    )["success"] is True
    create.assert_awaited_once()
