from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.review.assessment_feedback import ASSESSMENTS, FeedbackSubmission, PublishedAssessment
from agent.review.routes import get_assessment_feedback, submit_assessment_feedback
from agent.tools.submit_review_assessment_feedback import submit_review_assessment_feedback
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
    )
    await ASSESSMENTS.put("123", assessment)
    await ASSESSMENTS.put(
        "124", assessment.model_copy(update={"review_id": 124, "head_sha": "b" * 40})
    )
    with patch(
        "agent.review.assessment_feedback.require_repo_access_for_user", AsyncMock(return_value="t")
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
    assert await ASSESSMENTS.get("123") == assessment


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
            "agent.review.assessment_feedback.require_repo_access_for_user",
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
            "agent.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403)),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await get_assessment_feedback("private", "r", 1, 123, {"sub": "alice"})
    assert error.value.status_code == 403


async def test_feedback_tool_uses_private_owner_and_repo_permission(fake_store: FakeStore) -> None:
    with (
        patch(
            "agent.tools.submit_review_assessment_feedback.private_credential_login",
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
            "agent.tools.submit_review_assessment_feedback.private_credential_login",
            AsyncMock(return_value="Alice"),
        ),
        patch(
            "agent.review.assessment_feedback.require_repo_access_for_user",
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
            "agent.tools.submit_review_assessment_feedback.private_credential_login",
            AsyncMock(return_value="alice"),
        ),
        patch(
            "agent.review.assessment_feedback.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403)),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await submit_review_assessment_feedback(
            "o", "r", 1, 123, FeedbackSubmission(rating="helpful")
        )
    assert error.value.status_code == 403
