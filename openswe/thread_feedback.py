"""Storage for explicit thread feedback and historical submissions."""

from typing import Literal

from pydantic import BaseModel

from openswe.store import TypedStore

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
