"""Tool: ``propose_review_comment``. Drafts a line comment the user confirms before it posts."""

from pydantic import BaseModel

from openswe.tools.diff_lines import DiffRange, DiffSide, comment_text


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
    try:
        lines = DiffRange.of(file, line, start_line, side)
        text = comment_text(body)
    except ValueError as exc:
        return ProposeReviewCommentResult(proposed=False, error=str(exc))
    return ProposeReviewCommentResult(
        proposed=True,
        range=lines,
        body=text,
        note=(
            "Shown to the user as a draft. If they accept it, it joins their pending"
            " GitHub review, which posts only when they submit the review."
        ),
    )
