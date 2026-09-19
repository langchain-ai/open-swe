"""Record an authenticated user's explicit judgment about a published PR assessment."""

from typing import Literal, TypedDict

from agent.credential_scope import private_credential_login
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.risk import (
    ApprovalFeedback,
    RiskFeedbackSubmission,
    get_assessment,
    save_feedback,
)


class ReviewRiskFeedbackResult(TypedDict):
    status: Literal["saved"]
    assessment_id: str
    decision: ApprovalFeedback | None
    comment: str


async def submit_review_risk_feedback(
    owner: str,
    repo: str,
    pr_number: int,
    assessment_id: str,
    decision: ApprovalFeedback | None = None,
    comment: str = "",
) -> ReviewRiskFeedbackResult:
    """Record explicit user feedback about a published PR risk assessment."""
    login = await private_credential_login()
    if not login:
        raise ValueError(
            "Review risk feedback requires the authenticated owner of a private thread"
        )
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    record = await get_assessment(owner, repo, pr_number, assessment_id)
    if record is None or record.github_review_id is None:
        raise ValueError("Published risk assessment not found for this PR")
    feedback = await save_feedback(
        record, login=login, submission=RiskFeedbackSubmission(decision=decision, comment=comment)
    )
    return ReviewRiskFeedbackResult(
        status="saved",
        assessment_id=record.id,
        decision=feedback.decision,
        comment=feedback.comment,
    )
