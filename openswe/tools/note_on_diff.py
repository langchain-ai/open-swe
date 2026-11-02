"""Tool: ``note_on_diff``. Leaves a note on diff lines that only the user sees, never on GitHub."""

from pydantic import BaseModel

from openswe.tools.diff_lines import DiffRange, DiffSide, comment_text


class NoteOnDiffResult(BaseModel):
    noted: bool
    range: DiffRange | None = None
    body: str | None = None
    error: str | None = None


async def note_on_diff(
    file: str,
    line: int,
    body: str,
    start_line: int | None = None,
    side: DiffSide = "RIGHT",
) -> dict[str, object]:
    """Implement the `note_on_diff` tool."""
    # The page reads the note from this result; nothing else keeps it.
    try:
        result = NoteOnDiffResult(
            noted=True, range=DiffRange.of(file, line, start_line, side), body=comment_text(body)
        )
    except ValueError as exc:
        result = NoteOnDiffResult(noted=False, error=str(exc))
    return result.model_dump(mode="json", exclude_none=True)
