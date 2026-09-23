from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.review import author_guidance as guidance


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["private", None, "unknown"])
async def test_nonpublic_source_blocks_history_and_saved_guidance(monkeypatch, visibility):
    pull_request = SimpleNamespace(linked_threads=AsyncMock(return_value=["public", "private"]))
    monkeypatch.setattr(guidance.PullRequest, "get", AsyncMock(return_value=pull_request))
    threads = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda thread_id: {
                "metadata": {"visibility": "public" if thread_id == "public" else visibility}
            }
        ),
        get_state=AsyncMock(),
    )
    monkeypatch.setattr(guidance, "langgraph_client", lambda: SimpleNamespace(threads=threads))
    monkeypatch.setattr(guidance.postgres, "configured", lambda: True)
    saved = AsyncMock(return_value=[SimpleNamespace(summary="secret", quote="secret", author="a")])
    monkeypatch.setattr(guidance.GuidancePoint, "for_pull_request", saved)
    monkeypatch.setattr(guidance.GuidancePoint, "for_head", saved)

    assert await guidance.SteeringHistory.load("org", "repo", 1) is None
    assert await guidance.GuidanceView.for_pull_request("org", "repo", 1) == []
    assert await guidance.GuidanceView.for_head("org", "repo", 1, "sha") == []
    threads.get_state.assert_not_awaited()
    saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_visibility_rechecked_before_using_cached_history(monkeypatch):
    pull_request = SimpleNamespace(linked_threads=AsyncMock(return_value=["thread"]))
    monkeypatch.setattr(guidance.PullRequest, "get", AsyncMock(return_value=pull_request))
    get = AsyncMock(return_value={"metadata": {"visibility": "public"}})
    monkeypatch.setattr(
        guidance, "langgraph_client", lambda: SimpleNamespace(threads=SimpleNamespace(get=get))
    )
    history = guidance.SteeringHistory(
        request=guidance.HumanTurn(thread_id="thread", message_id="1", author="a", text="request"),
        follow_ups=[],
    )
    cached = AsyncMock(return_value=history)
    monkeypatch.setattr(guidance.ttl_cache, "cached", cached)
    monkeypatch.setattr(guidance.postgres, "configured", lambda: True)
    saved = AsyncMock(return_value=[SimpleNamespace(summary="summary", quote="quote", author="a")])
    monkeypatch.setattr(guidance.GuidancePoint, "for_pull_request", saved)
    monkeypatch.setattr(guidance.GuidancePoint, "for_head", saved)

    assert await guidance.SteeringHistory.load("org", "repo", 1) == history
    assert len(await guidance.GuidanceView.for_pull_request("org", "repo", 1)) == 1
    assert len(await guidance.GuidanceView.for_head("org", "repo", 1, "sha")) == 1
    get.return_value = {"metadata": {"visibility": "private"}}
    assert await guidance.SteeringHistory.load("org", "repo", 1) is None
    assert await guidance.GuidanceView.for_pull_request("org", "repo", 1) == []
    assert await guidance.GuidanceView.for_head("org", "repo", 1, "sha") == []
    cached.assert_awaited_once()
    get.side_effect = RuntimeError("metadata unavailable")
    assert await guidance.SteeringHistory.load("org", "repo", 1) is None
    assert await guidance.GuidanceView.for_pull_request("org", "repo", 1) == []
