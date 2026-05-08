"""Tool: ``add_finding``. Records one review finding on the reviewer thread."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_diff import is_range_in_diff
from ..reviewer_findings import (
    MAX_SUGGESTION_LINES,
    DiffSide,
    Finding,
    Severity,
    append_finding,
    clip_suggestion,
    get_thread_id_from_runtime,
    new_finding,
)


def add_finding(
    severity: str,
    category: str,
    file: str,
    description: str,
    start_line: int | None = None,
    end_line: int | None = None,
    suggestion: str | None = None,
    side: str = "RIGHT",
) -> dict[str, Any]:
    """Record a review finding on the reviewer thread.

    Findings persist on the reviewer thread's metadata so they survive sandbox
    eviction and are queryable across runs by the watch-mode reconciliation
    flow and the future UI.

    **When to use:** Once per distinct issue you find while reviewing the
    diff. Prefer one finding per issue, with a clear ``description`` and, when
    you can offer a concrete fix, a ``suggestion`` that exactly replaces lines
    ``start_line..end_line``.

    **In-diff only:** ``start_line..end_line`` must be inside the PR diff.
    File-level findings (both ``start_line`` and ``end_line`` None) are
    accepted but won't render as inline GitHub comments — only use when the
    issue truly isn't anchored to a line.

    Args:
        severity: One of ``informational``, ``low``, ``medium``, ``high``, ``critical``.
        category: Short category label (``correctness``, ``security``, ``perf``,
            ``style``, ``flag``, etc.). Free-form; used for grouping in the UI.
        file: Repo-relative path of the file the finding refers to.
        description: Markdown body the user sees.
        start_line: 1-based start line in the new (post-PR) file. Equal to
            ``end_line`` for single-line findings; less than ``end_line`` for
            ranges. Omit (with ``end_line``) for file-level findings.
        end_line: 1-based end line, inclusive. Defaults to ``start_line``.
        suggestion: Replacement text for ``start_line..end_line``. When set,
            the published GitHub comment includes a ```suggestion``` block so
            the user can click "Commit suggestion". **Only set this for small,
            obvious fixes that fit in 4 lines or fewer** (e.g. a one-liner
            rename, a missing guard, a typo). Longer suggestions are dropped
            because they read as rewrites rather than reviews — leave those
            cases as a description-only finding so the author can decide how
            to fix it.
        side: ``RIGHT`` (post-PR file, default) or ``LEFT`` (base file). Almost
            always ``RIGHT``.

    Returns:
        Dictionary with ``success``, ``finding_id`` and (on rejection) ``error``.
    """
    if start_line is not None and end_line is None:
        end_line = start_line
    if start_line is None and end_line is not None:
        start_line = end_line

    if severity not in {"informational", "low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity: {severity}"}
    if side not in {"LEFT", "RIGHT"}:
        return {"success": False, "error": f"Invalid side: {side}"}
    if start_line is not None and end_line is not None and end_line < start_line:
        return {"success": False, "error": "end_line must be >= start_line"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    diff_line_set = configurable.get("diff_line_set") if isinstance(configurable, dict) else None
    head_sha = configurable.get("head_sha", "") if isinstance(configurable, dict) else ""
    diff_text = configurable.get("diff_text", "") if isinstance(configurable, dict) else ""

    if isinstance(diff_line_set, dict) and not is_range_in_diff(
        diff_line_set, file, start_line, end_line
    ):
        return {
            "success": False,
            "error": (
                f"Finding range {file}:{start_line}-{end_line} is not part of the PR diff. "
                "Only review changes the PR introduces; do not flag pre-existing code."
            ),
        }

    diff_hunk: str | None = None
    if isinstance(diff_text, str) and diff_text:
        from ..reviewer_diff import extract_diff_hunk

        diff_hunk = extract_diff_hunk(diff_text, file, start_line, end_line)

    clipped_suggestion, suggestion_dropped = clip_suggestion(suggestion)

    finding: Finding = new_finding(
        severity=_cast_severity(severity),
        category=category,
        file=file,
        start_line=start_line,
        end_line=end_line,
        description=description,
        sha=str(head_sha) if isinstance(head_sha, str) else "",
        side=_cast_side(side),
        suggestion=clipped_suggestion,
        diff_hunk=diff_hunk,
    )

    thread_id = get_thread_id_from_runtime()
    asyncio.run(append_finding(thread_id, finding))
    result: dict[str, Any] = {"success": True, "finding_id": finding["id"]}
    if suggestion_dropped:
        result["suggestion_dropped"] = True
        result["warning"] = (
            f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap and was "
            "dropped — the finding was recorded with description only. Only "
            "include `suggestion` for small, obvious fixes."
        )
    return result


def _cast_severity(value: str) -> Severity:
    return value  # type: ignore[return-value]


def _cast_side(value: str) -> DiffSide:
    return value  # type: ignore[return-value]
