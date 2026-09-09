from typing import Any, Literal

from langchain_core.tools import tool

_MAX_PATH_CHARS = 1024


async def _show_in_diff(
    path: str,
    line: int | None = None,
    side: Literal["old", "new"] = "new",
) -> tuple[str, dict[str, Any]]:
    """Scroll the dashboard's diff view to a file and line."""
    cleaned = path.strip().lstrip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:].lstrip("/")
    if not cleaned or len(cleaned) > _MAX_PATH_CHARS:
        raise ValueError("path must be a repository-relative file path")
    if line is not None and line < 1:
        raise ValueError("line must be a positive line number")
    location = cleaned if line is None else f"{cleaned}:{line}"
    return (
        f"Scrolled the diff to {location}.",
        {"type": "show_in_diff", "path": cleaned, "line": line, "side": side},
    )


show_in_diff = tool(
    "show_in_diff",
    description="""Scroll the diff the user is looking at to a file, and optionally to a line
inside it, so they are looking at the code you are discussing. Call it as you reference a
location, then keep explaining in your reply — the tool only moves the view. `path` is
repository-relative. `line` is a line number as shown in the diff gutter, and `side` picks which
gutter it belongs to: "new" for added or unchanged lines, "old" for deleted lines. Only files
present in the diff can be shown; the view is left alone for anything else.""",
    response_format="content_and_artifact",
)(_show_in_diff)
