from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from openswe.review import goal_review as goal_review_module
from openswe.review.goal_review import GOAL_REVIEW_KEY, GoalReview

_FINDING = {
    "id": "f_1",
    "status": "open",
    "severity": "high",
    "title": "Wrong head",
    "file": "a.py",
    "description": "Anchors to the stale head.",
}


@pytest.mark.parametrize("handoff", [False, True])
async def test_a_ready_pr_hands_off_only_after_its_findings_are_published(
    monkeypatch: pytest.MonkeyPatch, handoff: bool
) -> None:
    metadata: dict[str, JsonValue] = {}
    told: list[str] = []

    async def set_metadata(thread_id: str, **fields: JsonValue) -> None:
        extra = fields.get("extra")
        if isinstance(extra, dict):
            metadata.update(extra)

    async def tell(self: GoalReview, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(goal_review_module, "list_findings", AsyncMock(return_value=[_FINDING]))
    monkeypatch.setattr(goal_review_module, "set_reviewer_thread_metadata", set_metadata)
    monkeypatch.setattr(GoalReview, "_tell", tell)
    monkeypatch.setattr("openswe.review.publish.settle_review_check_run", AsyncMock())
    monkeypatch.setattr("openswe.review.publish.clear_review_started_comment", AsyncMock())
    review = GoalReview(thread_id="agent-thread", handoff=handoff)

    result = await review.publish(
        reviewer_thread_id="reviewer-thread",
        owner="o",
        repo="r",
        pr_number=1,
        head_sha="a" * 40,
        token="t",
        severity_threshold="medium",
    )

    if handoff:
        assert result is None
        assert metadata == {} and told == []
    else:
        assert result is not None and result["open_count"] == 1
        assert "Wrong head" in told[0]
        told.clear()

    stored: dict[str, object] = {GOAL_REVIEW_KEY: review.model_dump(mode="json")}
    await GoalReview.published(
        "reviewer-thread", stored, owner="o", repo="r", pr_number=1, severity_threshold="medium"
    )

    if handoff:
        assert metadata == {GOAL_REVIEW_KEY: None}
        assert "1 open finding for its human reviewers" in told[0]
    else:
        assert GOAL_REVIEW_KEY not in metadata and told == []
