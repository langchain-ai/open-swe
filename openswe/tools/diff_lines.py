"""The diff lines and text the review page's comment tools take."""

from typing import Literal, Self

from pydantic import BaseModel

MAX_BODY_CHARS = 10_000

DiffSide = Literal["LEFT", "RIGHT"]


class DiffRange(BaseModel):
    file: str
    start_line: int
    end_line: int
    side: DiffSide

    @classmethod
    def of(cls, file: str, line: int, start_line: int | None, side: DiffSide) -> Self:
        """The lines from ``start_line`` (default ``line``) to ``line``; ValueError when invalid."""
        path = file.strip().lstrip("/")
        if not path:
            raise ValueError("file must be a path from the diff")
        start = start_line if start_line is not None else line
        if start < 1 or line < start:
            raise ValueError("start_line must be at least 1 and line must not precede it")
        return cls(file=path, start_line=start, end_line=line, side=side)


def comment_text(body: str) -> str:
    """The trimmed body; ValueError when it is empty or too long."""
    text = body.strip()
    if not text:
        raise ValueError("body must not be empty")
    if len(text) > MAX_BODY_CHARS:
        raise ValueError(f"body must be at most {MAX_BODY_CHARS} characters")
    return text
