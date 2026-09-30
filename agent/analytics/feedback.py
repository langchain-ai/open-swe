"""Capture user feedback submissions for analytics."""

from agent.analytics import directory, emitter
from agent.analytics.capture import fail_soft
from agent.analytics.identity import opaque_person
from agent.users import User


@fail_soft
async def record_feedback_submission(
    *,
    feedback_key: str,
    rating: int,
    source: str,
    run_key: str | None = None,
    github_login: str | None = None,
    user_email: str | None = None,
    slack_user_id: str | None = None,
) -> None:
    user = await User.for_identity("slack", slack_user_id) if slack_user_id else None
    person = await directory.resolve_person(
        github_login=github_login or (user.github_login if user else None),
        email=user_email or (user.email if user else None),
    )
    await emitter.feedback_submitted(
        feedback_key=feedback_key,
        rating=rating,
        source=source,
        run_key=run_key,
        user_id=person or opaque_person("slack", slack_user_id),
    )
