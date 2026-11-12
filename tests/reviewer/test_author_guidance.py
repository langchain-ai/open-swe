from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest

from openswe.review import author_guidance
from openswe.review.author_guidance import HumanTurn, SteeringHistory


@pytest.mark.parametrize("limit", [1, 40])
async def test_history_preserves_distinct_author_guide_feedback(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    monkeypatch.setattr(author_guidance, "MAX_FOLLOW_UPS", limit)
    request = HumanTurn(thread_id="builder", message_id="request", author="alice", text="Build it")
    feedback = HumanTurn(
        thread_id="guide", message_id="feedback", author="alice", text="Keep caching"
    )
    unlabeled = feedback.model_copy(update={"message_id": "", "text": "Keep retries too"})
    correction = request.model_copy(
        update={"message_id": "correction", "text": "Remove caching instead"}
    )
    button = feedback.model_copy(update={"message_id": "button", "text": "Looks good"})
    pull = SimpleNamespace(id=uuid7(), linked_threads=AsyncMock(return_value=["builder"]))
    monkeypatch.setattr(author_guidance.PullRequest, "get", AsyncMock(return_value=pull))
    session = AsyncMock()
    session.scalars.return_value = ["guide"]
    start = datetime(2026, 11, 1, tzinfo=UTC)
    session.execute.return_value = [
        SimpleNamespace(
            thread_id=turn.thread_id,
            message_id=turn.message_id,
            created_at=start + timedelta(days=index),
        )
        for index, turn in enumerate([request, feedback, unlabeled, correction])
    ]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(author_guidance.postgres, "session", lambda: context)
    monkeypatch.setattr(
        SteeringHistory,
        "_human_turns",
        AsyncMock(
            side_effect=[[request, correction], [request, button, feedback, feedback, unlabeled]]
        ),
    )

    history = await SteeringHistory.load("acme", "repo", 7)

    assert history is not None
    assert history.request == request
    assert history.follow_ups == [feedback, unlabeled, correction][-limit:]
    assert history.omitted_follow_ups == max(0, 3 - limit)
