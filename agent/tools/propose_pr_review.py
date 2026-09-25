"""Tool: ``propose_pr_review``. Drafts a pull request review the user confirms before it posts."""

from typing import Literal

from pydantic import BaseModel

from agent.tools.propose_review_comment import MAX_BODY_CHARS

ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]


class ProposePrReviewResult(BaseModel):
    proposed: bool
    event: ReviewEvent | None = None
    body: str | None = None
    note: str | None = None
    error: str | None = None


def _propose(event: ReviewEvent, body: str) -> ProposePrReviewResult:
    text = body.strip()
    if event != "APPROVE" and not text:
        return ProposePrReviewResult(
            proposed=False, error="a comment or change request needs a body"
        )
    if len(text) > MAX_BODY_CHARS:
        return ProposePrReviewResult(
            proposed=False, error=f"body must be at most {MAX_BODY_CHARS} characters"
        )
    return ProposePrReviewResult(
        proposed=True,
        event=event,
        body=text,
        note="Shown to the user as a draft. Nothing is submitted until they confirm it.",
    )


async def propose_pr_review(event: ReviewEvent, body: str = "") -> dict[str, object]:
    """Implement the `propose_pr_review` tool."""
    return _propose(event, body).model_dump(mode="json", exclude_none=True)
