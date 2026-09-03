"""REST API for a thread's self-review findings."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client

from agent.dashboard.oauth import require_same_origin_for_mutations, require_session
from agent.dashboard.thread_api import _thread_is_readable
from agent.review.inline_review import REVIEWS, InlineReview

logger = logging.getLogger(__name__)

inline_review_router = APIRouter(
    prefix="/dashboard/api/inline-review",
    tags=["inline-review"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


def _serialize(review: InlineReview) -> dict[str, Any]:
    return {
        "prNumber": review.pr_number,
        "prUrl": review.pr_url,
        "repoFullName": f"{review.owner}/{review.repo}" if review.owner else "",
        "headSha": review.head_sha,
        "status": review.status,
        "updatedAt": review.updated_at,
        "findings": [finding.model_dump(mode="json") for finding in review.sorted_findings()],
    }


@inline_review_router.get("/{thread_id}")
async def get_inline_review(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    """Every self-review this thread claimed, newest first."""
    try:
        thread = await get_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    if not _thread_is_readable(metadata if isinstance(metadata, dict) else {}):
        raise HTTPException(404, "thread not found")

    reviews = await REVIEWS.for_thread(thread_id)
    reviews.sort(key=lambda review: review.updated_at, reverse=True)
    return {"reviews": [_serialize(review) for review in reviews]}
