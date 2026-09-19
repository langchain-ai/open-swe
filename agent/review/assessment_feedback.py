"""Published assessments and each reviewer's explicit feedback."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.publish import ReviewAssessment
from agent.store import TypedStore, now_iso


class PublishedAssessment(ReviewAssessment):
    review_id: int
    owner: str
    repo: str
    pr_number: int
    approved: bool = False


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
) -> None:
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    assessment = await ASSESSMENTS.get(str(review_id))
    if (
        assessment is None
        or assessment.owner.lower() != owner.lower()
        or assessment.repo.lower() != repo.lower()
        or assessment.pr_number != pr_number
    ):
        raise HTTPException(404, "Assessment not found")


async def save_feedback(
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
    login: str,
    submission: FeedbackSubmission,
) -> AssessmentFeedback:
    await require_assessment_access(owner, repo, pr_number, review_id, login)
    feedback = AssessmentFeedback(
        rating=submission.rating,
        comment=submission.comment.strip(),
        login=login.lower(),
        updated_at=now_iso(),
    )
    await feedback_store(review_id).put(feedback.login, feedback)
    return feedback
