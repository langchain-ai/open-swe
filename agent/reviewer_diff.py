"""Diff utilities for the reviewer agent.

The reviewer needs three things from a PR diff:

1. The set of (file, line) tuples that are part of the diff, so ``add_finding``
   can validate at creation time rather than at GitHub-publish time.
2. The hunk text relevant to a given (file, start_line, end_line) range, so we
   can stash it on the Finding (``diff_hunk``) for rendering in the future UI
   without re-fetching from GitHub or the (evictable) sandbox.
3. A way to compute the diff in the sandbox between two SHAs, used both on
   first review (``base_sha..head_sha``) and on watched re-review
   (``last_reviewed_sha..new_head_sha``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

DiffSide = Literal["LEFT", "RIGHT"]

logger = logging.getLogger(__name__)


_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass(frozen=True)
class DiffHunk:
    """One hunk for one file in a unified diff.

    ``new_start``/``new_end`` are inclusive 1-based line numbers in the
    post-PR (RIGHT side) file. ``body`` is the raw hunk text including the
    ``@@`` header — what gets stored on a Finding's ``diff_hunk``.
    """

    file: str
    new_start: int
    new_end: int
    old_start: int
    old_end: int
    body: str


@dataclass(frozen=True)
class FileDiff:
    """All hunks for one file in a unified diff."""

    file: str
    hunks: tuple[DiffHunk, ...]


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file hunk records.

    Skips ``--- ``/``+++ `` and binary file markers. Returns one ``FileDiff``
    per file with at least one hunk; files with no hunks (e.g., pure renames)
    are dropped.
    """
    files: list[FileDiff] = []
    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        header_match = _DIFF_FILE_HEADER_RE.match(lines[i])
        if not header_match:
            i += 1
            continue
        file_path = header_match.group("b")
        i += 1
        # Skip metadata lines until first hunk or next file header
        hunks: list[DiffHunk] = []
        current_hunk_lines: list[str] = []
        current_meta: tuple[int, int, int, int] | None = None
        while i < len(lines) and not _DIFF_FILE_HEADER_RE.match(lines[i]):
            line = lines[i]
            hunk_match = _HUNK_HEADER_RE.match(line)
            if hunk_match:
                if current_meta is not None and current_hunk_lines:
                    hunks.append(
                        DiffHunk(
                            file=file_path,
                            old_start=current_meta[0],
                            old_end=current_meta[1],
                            new_start=current_meta[2],
                            new_end=current_meta[3],
                            body="\n".join(current_hunk_lines),
                        )
                    )
                old_start = int(hunk_match.group("old_start"))
                old_count = int(hunk_match.group("old_count") or "1")
                new_start = int(hunk_match.group("new_start"))
                new_count = int(hunk_match.group("new_count") or "1")
                # End line is inclusive; if count is 0 (deletion-only), end == start
                old_end = old_start + max(old_count - 1, 0)
                new_end = new_start + max(new_count - 1, 0)
                current_meta = (old_start, old_end, new_start, new_end)
                current_hunk_lines = [line]
            elif current_meta is not None:
                current_hunk_lines.append(line)
            i += 1
        if current_meta is not None and current_hunk_lines:
            hunks.append(
                DiffHunk(
                    file=file_path,
                    old_start=current_meta[0],
                    old_end=current_meta[1],
                    new_start=current_meta[2],
                    new_end=current_meta[3],
                    body="\n".join(current_hunk_lines),
                )
            )
        if hunks:
            files.append(FileDiff(file=file_path, hunks=tuple(hunks)))
    return files


def compute_diff_line_set(diff_text: str) -> dict[str, dict[str, set[int]]]:
    """Return ``{file: {"RIGHT": {new_lines}, "LEFT": {old_lines}}}`` for the
    lines covered by the diff.

    Inline GitHub review comments anchor either to a new-side line (``side =
    RIGHT``, the default — additions and context) or to an old-side line
    (``side = LEFT`` — deletions). ``add_finding`` and ``publish_review``
    validate a finding's ``(file, start_line..end_line, side)`` against the
    matching set so deleted-line bugs aren't wrongly rejected.
    """
    out: dict[str, dict[str, set[int]]] = {}
    for file_diff in parse_unified_diff(diff_text):
        sides = out.setdefault(file_diff.file, {"RIGHT": set(), "LEFT": set()})
        for hunk in file_diff.hunks:
            for line in range(hunk.new_start, hunk.new_end + 1):
                sides["RIGHT"].add(line)
            for line in range(hunk.old_start, hunk.old_end + 1):
                sides["LEFT"].add(line)
    return out


def extract_diff_hunk(
    diff_text: str,
    file: str,
    start_line: int | None,
    end_line: int | None,
) -> str | None:
    """Extract the hunk body covering ``file:start_line..end_line``.

    Returns ``None`` if no hunk overlaps. For file-level findings (both lines
    None) returns the first hunk in the file as best-effort context.
    """
    file_diffs = [fd for fd in parse_unified_diff(diff_text) if fd.file == file]
    if not file_diffs:
        return None
    hunks = file_diffs[0].hunks
    if not hunks:
        return None
    if start_line is None or end_line is None:
        return hunks[0].body
    for hunk in hunks:
        if hunk.new_start <= end_line and start_line <= hunk.new_end:
            return hunk.body
    return None


def is_range_in_diff(
    line_set: dict[str, dict[str, set[int]]],
    file: str,
    start_line: int | None,
    end_line: int | None,
    side: DiffSide = "RIGHT",
) -> bool:
    """Return True if every line in ``start_line..end_line`` is on the given
    side of the diff for ``file``.

    File-level findings (both None) are always allowed. ``side`` selects
    new-side lines (``RIGHT``, the default — additions/context) or old-side
    lines (``LEFT`` — deletions). Pass the finding's recorded ``side``.
    """
    if start_line is None and end_line is None:
        return True
    if start_line is None or end_line is None:
        return False
    file_sides = line_set.get(file)
    if not file_sides:
        return False
    side_lines = file_sides.get(side)
    if not side_lines:
        return False
    return all(line in side_lines for line in range(start_line, end_line + 1))


async def fetch_pr_diff(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    timeout: float = 30.0,
) -> str | None:
    """Fetch the PR's unified diff (base..head) from the GitHub REST API.

    Returns ``None`` if the request fails. This is the same diff GitHub
    validates against when posting inline review comments, so it's the right
    source for ``add_finding``'s in-diff anchor validation and for
    ``publish_review``'s 422 retry filter.
    """
    import httpx

    headers = {
        "Accept": "application/vnd.github.diff",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to fetch PR diff for %s/%s#%s", owner, repo, pr_number)
        return None
    return response.text


async def compute_diff_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    work_dir: str,
    base_ref: str,
    head_ref: str,
    *,
    merge_base: bool = False,
) -> str:
    """Run ``git diff`` inside the sandbox and return its stdout.

    Refs can be SHAs or branch names. Caller is responsible for ensuring both
    refs exist locally (e.g., having fetched the PR head).

    Args:
        merge_base: When ``True``, use three-dot ``base...head`` (the merge-base
            diff — what GitHub shows on the PR's "Files changed" tab). Use this
            for first review so we don't pick up changes that landed on the
            base branch after the PR diverged. When ``False``, use two-dot
            ``base..head`` — appropriate for re-review deltas where ``base`` is
            the previously reviewed SHA and we want exactly the commits added
            since.
    """
    operator = "..." if merge_base else ".."
    cmd = f"cd {work_dir} && git diff --no-color {base_ref}{operator}{head_ref}"
    result = await asyncio.to_thread(sandbox_backend.execute, cmd)
    exit_code = getattr(result, "exit_code", None)
    if exit_code not in (0, None):
        output = _stdout_from_result(result)
        raise RuntimeError(
            f"git diff failed (exit {exit_code}) for "
            f"{base_ref}{operator}{head_ref} in {work_dir}. Output:\n{output}"
        )
    return _stdout_from_result(result)


def _stdout_from_result(result: object) -> str:
    """Best-effort extraction of stdout from a sandbox execute() result.

    Different sandbox providers return different shapes; this normalizes them.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("stdout", "output", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    stdout = getattr(result, "stdout", None)
    if isinstance(stdout, str):
        return stdout
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    return ""
