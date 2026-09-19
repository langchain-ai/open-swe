"""Save the authenticated user's explicit assessment feedback."""

from agent.credential_scope import private_credential_login
from agent.review.assessment_feedback import AssessmentFeedback, FeedbackSubmission, save_feedback


async def submit_review_assessment_feedback(
    owner: str, repo: str, pr_number: int, review_id: int, feedback: FeedbackSubmission
) -> AssessmentFeedback:
    """Submit the user's rating and optional comment on a published review assessment."""
    login = await private_credential_login()
    if not login:
        raise ValueError("Review feedback requires the authenticated owner of a private task")
    return await save_feedback(owner, repo, pr_number, review_id, login, feedback)
