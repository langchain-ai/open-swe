"""Tool: ``propose_review_comment``. Drafts a line comment the user confirms before it posts."""

from pydantic import BaseModel

from agent.tools.show_in_diff import DiffRange, DiffSide, validate_range

MAX_BODY_CHARS = 10_000


class ProposeReviewCommentResult(BaseModel):
    proposed: bool
    range: DiffRange | None = None
    body: str | None = None
    note: str | None = None
    error: str | None = None


async def propose_review_comment(
    file: str,
    line: int,
    body: str,
    start_line: int | None = None,
    side: DiffSide = "RIGHT",
) -> dict[str, object]:
    """Implement the `propose_review_comment` tool."""
    return _propose(file, line, body, start_line, side).model_dump(mode="json", exclude_none=True)


def _propose(
    file: str, line: int, body: str, start_line: int | None, side: DiffSide
) -> ProposeReviewCommentResult:
    text = body.strip()
    if not text:
        return ProposeReviewCommentResult(proposed=False, error="body must not be empty")
    if len(text) > MAX_BODY_CHARS:
        return ProposeReviewCommentResult(
            proposed=False, error=f"body must be at most {MAX_BODY_CHARS} characters"
        )
    checked = validate_range(file, start_line if start_line is not None else line, line, side)
    if isinstance(checked, str):
        return ProposeReviewCommentResult(proposed=False, error=checked)
    return ProposeReviewCommentResult(
        proposed=True,
        range=checked,
        body=text,
        note=(
            "Shown to the user as a draft. If they accept it, it joins their pending"
            " GitHub review, which posts only when they submit the review."
        ),
    )
