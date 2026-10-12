"""Published assessments and each reviewer's explicit feedback."""

import logging
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.review.publish import ReviewAssessment
from openswe.store import TypedStore, now_iso
from openswe.utils.langsmith import create_langsmith_feedback

logger = logging.getLogger(__name__)


class PublishedAssessment(ReviewAssessment):
    review_id: int
    owner: str
    repo: str
    pr_number: int
    approved: bool = False
    dry_run: bool = False
    run_id: str | None = None


class FeedbackSubmission(BaseModel):
    rating: Literal["helpful", "unhelpful"]
    comment: str = Field(default="", max_length=3000)


class AssessmentFeedback(FeedbackSubmission):
    login: str
    updated_at: str


ASSESSMENTS = TypedStore(("review_assessments",), PublishedAssessment)


def feedback_store(review_id: int) -> TypedStore[AssessmentFeedback]:
    return TypedStore(("review_assessment_feedback", str(review_id)), AssessmentFeedback)


async def require_assessment_access(
    owner: str, repo: str, pr_number: int, review_id: int, login: str
) -> PublishedAssessment:
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    assessment = await ASSESSMENTS.get(str(review_id))
    if (
        assessment is None
        or assessment.owner.lower() != owner.lower()
        or assessment.repo.lower() != repo.lower()
        or assessment.pr_number != pr_number
    ):
        raise HTTPException(404, "Assessment not found")
    return assessment


async def save_feedback(
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
    login: str,
    submission: FeedbackSubmission,
) -> AssessmentFeedback:
    assessment = await require_assessment_access(owner, repo, pr_number, review_id, login)
    feedback = AssessmentFeedback(
        rating=submission.rating,
        comment=submission.comment.strip(),
        login=login.lower(),
        updated_at=now_iso(),
    )
    await feedback_store(review_id).put(feedback.login, feedback)
    if assessment.run_id:
        try:
            saved = await create_langsmith_feedback(
                assessment.run_id,
                f"review_assessment:{review_id}:{feedback.login}",
                score=1.0 if feedback.rating == "helpful" else 0.0,
                comment=feedback.comment or None,
                source_info={
                    "source": "review_assessment",
                    "review_id": review_id,
                    "user_login": feedback.login,
                    "owner": owner,
                    "repo": repo,
                    "pr_number": pr_number,
                },
            )
            if not saved:
                logger.warning(
                    "Review assessment trace feedback was not saved", extra={"review_id": review_id}
                )
        except Exception:
            logger.exception(
                "Failed to save review assessment trace feedback", extra={"review_id": review_id}
            )
    return feedback
