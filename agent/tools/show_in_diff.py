"""Tool: ``show_in_diff``. Scrolls the user's review page to a line range and pulses it."""

from typing import Literal

from pydantic import BaseModel

DiffSide = Literal["LEFT", "RIGHT"]


class DiffRange(BaseModel):
    file: str
    start_line: int
    end_line: int
    side: DiffSide


class ShowInDiffResult(BaseModel):
    shown: bool
    range: DiffRange | None = None
    error: str | None = None


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


async def show_in_diff(
    file: str, start_line: int, end_line: int | None = None, side: DiffSide = "RIGHT"
) -> dict[str, object]:
    """Implement the `show_in_diff` tool."""
    checked = validate_range(file, start_line, end_line, side)
    result = (
        ShowInDiffResult(shown=False, error=checked)
        if isinstance(checked, str)
        else ShowInDiffResult(shown=True, range=checked)
    )
    return result.model_dump(mode="json", exclude_none=True)
