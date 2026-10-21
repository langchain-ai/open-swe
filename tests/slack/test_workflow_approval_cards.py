import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.slack import workflow_approval_cards as cards


@pytest.fixture
def delivery(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    metadata = {
        "worker": {"owner_login": "alice", "visibility": "public"},
        "coordinator": {
            "owner_login": "alice",
            "source_context": {"slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.1"}},
        },
    }
    record = {
        "fingerprint": "diff-hash",
        "status": "pending",
        "repo": "langchain-ai/open-swe",
        "branch": "feature",
        "files": [".github/workflows/ci.yml"],
    }
    post = AsyncMock(return_value="1700000000.2")
    dm = AsyncMock(return_value=True)
    wake = AsyncMock(return_value="coordinator")
    monkeypatch.setattr(cards, "fetch_thread_metadata", AsyncMock(side_effect=metadata.__getitem__))
    monkeypatch.setattr(
        cards.TaskMembership,
        "context_for_thread",
        AsyncMock(
            return_value=SimpleNamespace(task=SimpleNamespace(coordinator_thread_id="coordinator"))
        ),
    )
    monkeypatch.setattr(
        cards.User, "for_login", AsyncMock(return_value=SimpleNamespace(slack_user_id="U123"))
    )
    monkeypatch.setattr(
        cards, "get_workflow_push_approvals", AsyncMock(return_value={"diff-hash": record})
    )
    monkeypatch.setattr(cards, "mark_workflow_push_notified", AsyncMock())
    monkeypatch.setattr(cards, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(cards, "send_dm", dm)
    monkeypatch.setattr(cards, "wake_thread_owner", wake)
    return SimpleNamespace(metadata=metadata, record=record, post=post, dm=dm, wake=wake)


async def test_worker_hands_approval_to_coordinator_without_posting(
    delivery: SimpleNamespace,
) -> None:
    assert await cards.propagate_workflow_approval("worker", delivery.record)
    assert delivery.post.await_count == 0
    assert delivery.dm.await_count == 0
    args = delivery.wake.await_args.args
    assert args[:3] == ("C123", "1700000000.1", "U123")
    assert 'thread_id="worker"' in args[3]
    assert 'fingerprint="diff-hash"' in args[3]


async def test_failed_handoff_sends_owner_dm_with_target_and_origin(
    delivery: SimpleNamespace,
) -> None:
    delivery.wake.side_effect = RuntimeError("dispatch unavailable")
    assert await cards.propagate_workflow_approval("worker", delivery.record)
    assert delivery.post.await_count == 0
    kwargs = delivery.dm.await_args.kwargs
    assert kwargs["origin"].location == ("C123", "1700000000.1")
    buttons = kwargs["blocks"][-1]["elements"]
    assert json.loads(buttons[0]["value"])["thread_id"] == "worker"


async def test_coordinator_shares_original_worker_card(delivery: SimpleNamespace) -> None:
    result = await cards.share_workflow_approval(
        "worker", "diff-hash", current_thread_id="coordinator", actor_login="alice"
    )
    assert result["status"] == "shared"
    kwargs = delivery.post.await_args.kwargs
    assert kwargs["agent_thread_id"] == "coordinator"
    button = kwargs["blocks"][-1]["elements"][0]
    assert json.loads(button["value"])["thread_id"] == "worker"


@pytest.mark.parametrize("restriction", ["nonowner", "unrelated", "private", "terminal"])
async def test_share_denies_unsafe_destination_or_stale_request(
    delivery: SimpleNamespace, restriction: str
) -> None:
    current = "coordinator"
    actor = "alice"
    if restriction == "nonowner":
        actor = "mallory"
    elif restriction == "unrelated":
        current = "unrelated"
    elif restriction == "private":
        delivery.metadata["worker"]["visibility"] = "private"
    else:
        delivery.record["status"] = "approved"
    with pytest.raises(HTTPException):
        await cards.share_workflow_approval(
            "worker", "diff-hash", current_thread_id=current, actor_login=actor
        )
    assert delivery.post.await_count == 0


async def test_private_worker_falls_back_to_dm_without_public_origin(
    delivery: SimpleNamespace,
) -> None:
    delivery.metadata["worker"]["visibility"] = "private"
    assert await cards.propagate_workflow_approval("worker", delivery.record)
    assert delivery.wake.await_count == 0
    assert delivery.dm.await_args.kwargs["origin"] is None
