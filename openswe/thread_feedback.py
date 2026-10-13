"""Storage for explicit thread feedback and historical submissions."""

from typing import Any, Literal

from pydantic import BaseModel

from openswe.analytics.feedback import record_feedback_submission
from openswe.store import TypedStore
from openswe.utils.langsmith import create_langsmith_thread_feedback
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

Rating = Literal["bad", "good", "other"]


class Feedback(BaseModel):
    status: Literal["pending", "ready", "completed", "dismissed"] = "pending"
    event_id: str = ""
    answer_run_id: str = ""
    activity_at_ms: int = 0
    rating: Rating | None = None
    comment: str = ""


def feedback_store() -> TypedStore[Feedback]:
    return TypedStore(("thread_feedback",), Feedback)


async def record_thread_feedback(
    thread_id: str,
    rating: Literal["bad", "good"],
    comment: str,
    *,
    event_id: str,
    run_id: str,
    source: str,
    source_info: dict[str, Any],
    github_login: str | None = None,
    user_email: str | None = None,
    slack_user_id: str | None = None,
) -> bool:
    """Save a person's explicit rating of a thread; returns whether LangSmith received it."""
    async with agent_thread_pr_state_lock(langgraph_client(), thread_id):
        record = await feedback_store().get(thread_id)
        record = record or Feedback(event_id=event_id, answer_run_id=run_id)
        record.status = "completed"
        record.rating = rating
        record.comment = comment
        await feedback_store().put(thread_id, record)
        exported = await create_langsmith_thread_feedback(
            thread_id,
            "rating",
            score=1.0 if rating == "good" else 0.0,
            comment=comment or None,
            source_info=source_info,
        )
        await record_feedback_submission(
            feedback_key=f"thread:{thread_id}",
            rating=5 if rating == "good" else 1,
            source=source,
            run_key=run_id or None,
            github_login=github_login,
            user_email=user_email,
            slack_user_id=slack_user_id,
        )
    return exported
