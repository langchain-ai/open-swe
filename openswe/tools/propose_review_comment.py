"""Tool: ``propose_review_comment``. Drafts a line comment the user confirms before it posts."""

from typing import Literal

from pydantic import BaseModel

MAX_BODY_CHARS = 10_000

DiffSide = Literal["LEFT", "RIGHT"]


class DiffRange(BaseModel):
    file: str
    start_line: int
    end_line: int
    side: DiffSide


def validate_range(
    file: str, start_line: int, end_line: int | None, side: DiffSide
) -> DiffRange | str:
    """The normalized range, or why it is invalid."""
    path = file.strip().lstrip("/")
    if not path:
        return "file must be a path from the diff"
    end = end_line if end_line is not None else start_line
    if start_line < 1 or end < start_line:
        return "start_line must be at least 1 and end_line must not precede it"
    return DiffRange(file=path, start_line=start_line, end_line=end, side=side)


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
