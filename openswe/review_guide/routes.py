"""The dashboard's side of an interactive review: the reader's "Next" on a chunk shown there."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.review_guide.advance import advance
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.users import User

router = APIRouter(tags=["interactive-review"])


class InteractiveReviewNext(BaseModel):
    # The chunk's id, as the dashboard card received it in the tool's result.
    message_ts: str


class InteractiveReviewNextResult(BaseModel):
    accepted: bool = True


@router.post("/threads/{thread_id}/interactive-review/next")
@audit_endpoint
async def api_interactive_review_next(
    thread_id: str,
    body: InteractiveReviewNext,
    session: dict[str, str] = SESSION_DEP,
) -> InteractiveReviewNextResult:
    """Approve the chunk on screen as its reader, and go on to what comes next."""
    guide = await ReviewGuideSession.get(thread_id)
    if guide is None or guide.closed:
        raise HTTPException(404, "This thread has no interactive review in progress")
    reader = await User.for_login("github", str(session["sub"]))
    if reader is None or reader.id != guide.user_id:
        raise HTTPException(403, "Only the person being walked through can move on")
    walk = guide.walk
    if walk is None or walk.on_screen is None or walk.on_screen.message_ts != body.message_ts:
        raise HTTPException(409, "That chunk is no longer on screen")
    await advance(guide, body.message_ts)
    return InteractiveReviewNextResult()
