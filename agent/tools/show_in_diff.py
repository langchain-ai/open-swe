"""Tool: scroll the dashboard's diff to a file and line, and read back that view.

The artifact drives the UI; the tool result renders the same hunk the user is now
looking at, gutter line numbers and all, so the agent is reasoning about what is
actually on their screen rather than about a location it named blind.
"""

import logging
import posixpath
import shlex
from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel

from agent.prompts import load_prompt
from agent.review.diff import DiffHunk, parse_unified_diff
from agent.run_config import RunConfig
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.state import get_sandbox_backend

logger = logging.getLogger(__name__)

_MAX_PATH_CHARS = 1024
_MAX_ROWS = 80
_MAX_ROW_CHARS = 300
# The review chat has no sandbox: its PR diff is seeded as a virtual file.
_SEEDED_PR_DIFF = "/pr/diff.patch"

Side = Literal["old", "new"]


class _SeededFile(BaseModel):
    content: str = ""


class _SeededFiles(BaseModel):
    files: dict[str, _SeededFile] = {}


# `HEAD` alone would miss work the agent already committed, and the base alone
# would miss the working tree; diffing the merge base covers both scopes the
# Changes panel can be showing. A file git has never seen has no diff at all, so
# it is rendered against /dev/null the way the panel renders it.
_FILE_DIFF_SCRIPT = """\
cd {directory} 2>/dev/null || exit 3
git rev-parse --git-dir >/dev/null 2>&1 || exit 3
default=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null || echo origin/main)
base=$(git merge-base HEAD "$default" 2>/dev/null)
git diff --no-color --no-ext-diff "${{base:-HEAD}}" -- {path}
git ls-files --error-unmatch -- {path} >/dev/null 2>&1 ||
  git diff --no-color --no-ext-diff --no-index -- /dev/null {path} 2>/dev/null
"""


def _seeded_pr_diff(state: dict[str, Any] | None) -> str | None:
    if not state:
        return None
    seeded = _SeededFiles.model_validate(state).files.get(_SEEDED_PR_DIFF)
    content = seeded.content.strip() if seeded else ""
    return content or None


async def _sandbox_diff(path: str) -> str | None:
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return None
    backend = await get_sandbox_backend(thread_id)
    work_dir = await resolve_sandbox_work_dir(backend)
    repo_name = cfg.repo.name if cfg.repo else ""
    directories = [posixpath.join(work_dir, repo_name)] if repo_name else []
    directories.append(work_dir)
    for directory in directories:
        result = await backend.aexecute(
            _FILE_DIFF_SCRIPT.format(directory=shlex.quote(directory), path=shlex.quote(path)),
            timeout=20,
        )
        if result.exit_code == 3:
            continue
        if result.output.strip():
            return result.output
    return None


async def _resolve_diff(path: str, state: dict[str, Any] | None) -> str | None:
    seeded = _seeded_pr_diff(state)
    if seeded:
        return seeded
    try:
        return await _sandbox_diff(path)
    except Exception:  # noqa: BLE001
        logger.warning("show_in_diff could not read the diff", extra={"file_path": path})
        return None


def _matches(diff_path: str, requested: str) -> bool:
    return (
        diff_path == requested
        or diff_path.endswith(f"/{requested}")
        or requested.endswith(f"/{diff_path}")
    )


def _covers(hunk: DiffHunk, line: int, side: Side) -> bool:
    if side == "old":
        return hunk.old_start <= line <= hunk.old_end
    return hunk.new_start <= line <= hunk.new_end


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_ROW_CHARS else f"{text[:_MAX_ROW_CHARS]}…"


def _render_hunk(hunk: DiffHunk, line: int | None, side: Side) -> str:
    """The hunk as the diff view shows it: both gutters, the target line marked."""
    body = hunk.body.splitlines()
    rows: list[tuple[int | None, int | None, str, bool]] = []
    old_no, new_no = hunk.old_start, hunk.new_start
    for raw in body[1:]:
        marker, text = raw[:1], raw[1:]
        if marker == "\\":
            continue
        if marker == "-":
            rows.append((old_no, None, f"- {text}", side == "old" and old_no == line))
            old_no += 1
        elif marker == "+":
            rows.append((None, new_no, f"+ {text}", side == "new" and new_no == line))
            new_no += 1
        else:
            hit = old_no == line if side == "old" else new_no == line
            rows.append((old_no, new_no, f"  {text}", hit))
            old_no += 1
            new_no += 1

    center = next((i for i, row in enumerate(rows) if row[3]), len(rows) // 2)
    start = max(0, min(center - _MAX_ROWS // 2, len(rows) - _MAX_ROWS))
    window = rows[start : start + _MAX_ROWS]
    rendered = [body[0] if body else ""]
    if start > 0:
        rendered.append(f"… {start} earlier lines in this hunk")
    rendered += [
        f"{'>' if hit else ' '} {old or '':>6} {new or '':>6} {_clip(text)}"
        for old, new, text, hit in window
    ]
    remaining = len(rows) - start - len(window)
    if remaining > 0:
        rendered.append(f"… {remaining} further lines in this hunk")
    return "\n".join(rendered)


def _view(diff_text: str | None, path: str, line: int | None, side: Side) -> str:
    location = path if line is None else f"{path}:{line}"
    if not diff_text:
        return (
            f"Asked the diff to show {location}, but the diff itself could not be read "
            "here, so there is nothing to show back."
        )
    file_diffs = [fd for fd in parse_unified_diff(diff_text) if _matches(fd.file, path)]
    if not file_diffs:
        return f"{path} is not in the diff, so the view did not move."
    hunks = file_diffs[0].hunks
    hunk = hunks[0] if line is None else next((h for h in hunks if _covers(h, line, side)), None)
    if hunk is None:
        ranges = ", ".join(
            f"{h.old_start}-{h.old_end}" if side == "old" else f"{h.new_start}-{h.new_end}"
            for h in hunks[:10]
        )
        return (
            f"Line {line} is not in the diff for {path}, so the view moved to the file "
            f"rather than the line. {side.capitalize()}-side ranges it covers: {ranges}."
        )
    heading = (
        f"Showing {path}, centered on {side}-side line {line}."
        if line is not None
        else f"Showing {path}, centered on its first hunk."
    )
    extra = (
        f"\n\n{len(hunks) - 1} other hunk{'' if len(hunks) == 2 else 's'} in this file."
        if len(hunks) > 1
        else ""
    )
    return f"{heading}\n\n{_render_hunk(hunk, line, side)}{extra}"


async def _show_in_diff(
    path: str,
    line: int | None = None,
    side: Side = "new",
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> tuple[str, dict[str, Any]]:
    """Scroll the dashboard's diff view to a file and line."""
    cleaned = path.strip().lstrip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:].lstrip("/")
    if not cleaned or len(cleaned) > _MAX_PATH_CHARS:
        raise ValueError("path must be a repository-relative file path")
    if line is not None and line < 1:
        raise ValueError("line must be a positive line number")
    diff_text = await _resolve_diff(cleaned, state)
    return (
        _view(diff_text, cleaned, line, side),
        {"type": "show_in_diff", "path": cleaned, "line": line, "side": side},
    )


show_in_diff = tool(
    "show_in_diff",
    description=load_prompt("tools/show_in_diff.md"),
    response_format="content_and_artifact",
)(_show_in_diff)
