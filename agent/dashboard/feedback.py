"""Private web feedback on completed agent threads."""

from typing import Any, Literal, Self

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from agent.dashboard.oauth import require_same_origin_for_mutations, require_session
from agent.dashboard.plan_api import fetch_thread_metadata
from agent.dashboard.threads.summary import thread_is_readable
from agent.dashboard.user_mappings import login_for_slack_id
from agent.source_context import SourceContext
from agent.store import TypedStore, now_ms
from agent.thread_feedback import complete_feedback_prompt, feedback_prompt_status
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

FeedbackRating = Literal["bad", "good", "other"]

feedback_router = APIRouter(
    prefix="/threads",
    tags=["thread-feedback"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


class FeedbackSubmission(BaseModel):
    rating: FeedbackRating
    comment: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def validate_comment(self) -> Self:
        self.comment = self.comment.strip()
        if self.rating == "other" and not self.comment:
            raise ValueError("Add a comment when choosing Other.")
        return self


class WebThreadFeedback(BaseModel):
    status: Literal["completed", "dismissed"]
    rating: FeedbackRating | None = None
    comment: str = ""
    submitted_at: int


def _store(thread_id: str) -> TypedStore[WebThreadFeedback]:
    return TypedStore(("web_thread_feedback", thread_id), WebThreadFeedback)


def _response(
    status: str, prompt_at: int | None = None, record: WebThreadFeedback | None = None
) -> dict[str, Any]:
    return {
        "status": status,
        "promptAt": prompt_at,
        "rating": record.rating if record else None,
        "comment": record.comment if record else "",
    }


async def _is_initiator(thread_id: str, login: str) -> bool:
    metadata = await fetch_thread_metadata(thread_id)
    if not thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")
    initiator = metadata.get("feedback_initiator_login")
    if isinstance(initiator, str) and initiator.strip():
        return initiator.strip().lower() == login
    origin = SourceContext.from_metadata(metadata).slack_thread
    if origin and origin.triggering_user_id:
        mapped = await login_for_slack_id(origin.triggering_user_id)
        return bool(mapped and mapped.strip().lower() == login)
    return False


@feedback_router.get("/{thread_id}/feedback")
async def get_thread_feedback(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    login = str(session["sub"]).strip().lower()
    if not await _is_initiator(thread_id, login):
        return _response("unavailable")
    record = await _store(thread_id).get(login)
    if record:
        await complete_feedback_prompt(thread_id, record.status)
        return _response(record.status, record=record)
    status, prompt_at = await feedback_prompt_status(thread_id)
    return _response(status, prompt_at)


async def _save_feedback(
    thread_id: str,
    login: str,
    submission: FeedbackSubmission | None,
) -> dict[str, Any]:
    if not await _is_initiator(thread_id, login):
        raise HTTPException(403, "Only the thread initiator can give feedback.")
    async with agent_thread_pr_state_lock(langgraph_client(), thread_id):
        record = await _store(thread_id).get(login)
        if record is None:
            status, _ = await feedback_prompt_status(thread_id)
            if status in {"completed", "dismissed"}:
                return _response(status)
            if status != "ready":
                raise HTTPException(409, "Feedback is not available for this thread yet.")
            record = WebThreadFeedback(
                status="completed" if submission else "dismissed",
                rating=submission.rating if submission else None,
                comment=submission.comment if submission else "",
                submitted_at=now_ms(),
            )
            await _store(thread_id).put(login, record)
    await complete_feedback_prompt(thread_id, record.status)
    return _response(record.status, record=record)


@feedback_router.post("/{thread_id}/feedback")
async def submit_thread_feedback(
    thread_id: str,
    submission: FeedbackSubmission,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await _save_feedback(thread_id, str(session["sub"]).strip().lower(), submission)


@feedback_router.post("/{thread_id}/feedback/dismiss")
async def dismiss_thread_feedback(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    return await _save_feedback(thread_id, str(session["sub"]).strip().lower(), None)
