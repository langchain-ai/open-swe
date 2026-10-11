from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from openswe.review.assessment_feedback import ASSESSMENTS, FeedbackSubmission, PublishedAssessment
from openswe.review.routes import get_assessment_feedback, submit_assessment_feedback
from openswe.tools.submit_review_assessment_feedback import submit_review_assessment_feedback
from tests.conftest import FakeStore


async def test_feedback_is_per_person_and_published_assessment(fake_store: FakeStore) -> None:
    assessment = PublishedAssessment(
        review_id=123,
        owner="o",
        repo="r",
        pr_number=1,
        head_sha="a" * 40,
        risk_score=1,
        decision="would_approve",
        explanation="Docs only.",
        run_id="c" * 36,
    )
    await ASSESSMENTS.put("123", assessment)
    await ASSESSMENTS.put(
        "124", assessment.model_copy(update={"review_id": 124, "head_sha": "b" * 40})
    )
    with (
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
        patch(
            "openswe.review.assessment_feedback.create_langsmith_feedback", new_callable=AsyncMock
        ) as trace_feedback,
    ):
        first = await submit_assessment_feedback(
            "o",
            "r",
            1,
            123,
            FeedbackSubmission(rating="helpful", comment="  Clear rationale  "),
            {"sub": "alice"},
        )
        await submit_assessment_feedback(
            "o",
            "r",
            1,
            123,
            FeedbackSubmission(rating="unhelpful", comment="Missed a migration"),
            {"sub": "bob"},
        )
        saved = await get_assessment_feedback("o", "r", 1, 123, {"sub": "alice"})
        assert saved == first
        assert saved.comment == "Clear rationale"
        assert await get_assessment_feedback("o", "r", 1, 124, {"sub": "alice"}) is None
        edited = await submit_assessment_feedback(
            "o",
            "r",
            1,
            123,
            FeedbackSubmission(rating="unhelpful", comment="Changed my mind"),
            {"sub": "alice"},
        )
        assert edited.comment == "Changed my mind"
        bob = await get_assessment_feedback("o", "r", 1, 123, {"sub": "bob"})
        assert bob and bob.comment == "Missed a migration"
        assert trace_feedback.await_count == 3
        assert trace_feedback.await_args_list[0].args == (
            "c" * 36,
            f"review_assessment:123:{'a' * 40}:alice",
        )
        assert trace_feedback.await_args_list[0].kwargs["score"] == 1.0
        assert trace_feedback.await_args_list[0].kwargs["comment"] == "Clear rationale"
        assert trace_feedback.await_args_list[1].kwargs["score"] == 0.0
        assert trace_feedback.await_args_list[2].args == trace_feedback.await_args_list[0].args
        assert trace_feedback.await_args_list[2].kwargs["score"] == 0.0
        await ASSESSMENTS.put("123", assessment.model_copy(update={"head_sha": "d" * 40}))
        assert await get_assessment_feedback("o", "r", 1, 123, {"sub": "alice"}) is None
        await submit_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="helpful"), {"sub": "alice"}
        )
        assert trace_feedback.await_args_list[-1].args[1] == (
            f"review_assessment:123:{'d' * 40}:alice"
        )
        await ASSESSMENTS.put("123", assessment)
        assert await get_assessment_feedback("o", "r", 1, 123, {"sub": "alice"}) == edited
    assert await ASSESSMENTS.get("123") == assessment


async def test_trace_failure_does_not_lose_saved_feedback(fake_store: FakeStore) -> None:
    await ASSESSMENTS.put(
        "123",
        PublishedAssessment(
            review_id=123,
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="a" * 40,
            risk_score=1,
            decision="would_approve",
            explanation="Docs only.",
            run_id="c" * 36,
        ),
    )
    assessment = await ASSESSMENTS.get("123")
    with (
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
        patch.object(
            ASSESSMENTS,
            "get",
            AsyncMock(side_effect=[assessment, RuntimeError("store unavailable")]),
        ),
        patch(
            "openswe.review.assessment_feedback.create_langsmith_feedback",
            AsyncMock(side_effect=RuntimeError("trace unavailable")),
        ),
    ):
        saved = await submit_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="unhelpful"), {"sub": "alice"}
        )
    with patch(
        "openswe.review.assessment_feedback.require_repo_access_for_user",
        AsyncMock(return_value="t"),
    ):
        assert await get_assessment_feedback("o", "r", 1, 123, {"sub": "alice"}) == saved


async def test_feedback_cannot_cross_repository_scope(fake_store: FakeStore) -> None:
    await ASSESSMENTS.put(
        "123",
        PublishedAssessment(
            review_id=123,
            owner="private",
            repo="r",
            pr_number=1,
            head_sha="a" * 40,
            risk_score=1,
            decision="would_approve",
            explanation="Docs only.",
        ),
    )
    with (
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await submit_assessment_feedback(
            "public", "r", 1, 123, FeedbackSubmission(rating="helpful"), {"sub": "alice"}
        )
    assert error.value.status_code == 404
    with (
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403)),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await get_assessment_feedback("private", "r", 1, 123, {"sub": "alice"})
    assert error.value.status_code == 403


async def test_feedback_tool_uses_private_owner_and_repo_permission(fake_store: FakeStore) -> None:
    with (
        patch(
            "openswe.tools.submit_review_assessment_feedback.private_credential_login",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ValueError, match="authenticated owner"),
    ):
        await submit_review_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="helpful")
        )

    await ASSESSMENTS.put(
        "123",
        PublishedAssessment(
            review_id=123,
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="a" * 40,
            risk_score=1,
            decision="would_approve",
            explanation="Docs only.",
        ),
    )
    with (
        patch(
            "openswe.tools.submit_review_assessment_feedback.private_credential_login",
            AsyncMock(return_value="Alice"),
        ),
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(return_value="t"),
        ),
    ):
        saved = await submit_review_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="helpful", comment="Clear")
        )
        assert saved.login == "alice"
        assert await get_assessment_feedback("o", "r", 1, 123, {"sub": "ALICE"}) == saved
    with (
        patch(
            "openswe.tools.submit_review_assessment_feedback.private_credential_login",
            AsyncMock(return_value="alice"),
        ),
        patch(
            "openswe.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403)),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await submit_review_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="helpful")
        )
    assert error.value.status_code == 403
