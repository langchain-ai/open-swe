"""Tool: ``add_finding``. Records one review finding on the reviewer thread."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_diff import is_range_in_diff
from ..reviewer_findings import (
    DEFAULT_FINDING_TITLE,
    MAX_SUGGESTION_LINES,
    Confidence,
    DiffSide,
    Finding,
    ReviewerThreadMissingError,
    Severity,
    append_finding,
    clip_suggestion,
    get_thread_id_from_runtime,
    new_finding,
    normalize_finding_title,
    resolve_review_head_sha,
    thread_missing_tool_result,
)


def add_finding(
    severity: str,
    confidence: str,
    category: str,
    title: str,
    description: str,
    file: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    suggestion: str | None = None,
    side: str = "RIGHT",
    file_path: str | None = None,
) -> dict[str, Any]:
    """Record a review finding on the reviewer thread.

    Findings persist on the reviewer thread's metadata so they survive sandbox
    eviction and are queryable across runs by the watch-mode reconciliation
    flow and the future UI.

    **When to use:** Once per distinct issue you find while reviewing the
    diff. Prefer one finding per issue, with a concise generated ``title`` that
    names the failure mode, a clear ``description`` body, and, when you can
    offer a concrete fix, a ``suggestion`` that exactly replaces lines
    ``start_line..end_line``.

    **In-diff only:** ``start_line..end_line`` must be inside the PR diff.
    Findings anchored to lines outside the diff are rejected (out-of-diff
    findings are disabled). File-level findings (both ``start_line`` and
    ``end_line`` None) are accepted but won't render as inline GitHub
    comments — only use when the issue truly isn't anchored to a line.

    Args:
        severity: One of ``low``, ``medium``, ``high``, ``critical``.
        confidence: One of ``low``, ``medium``, ``high``.
        category: Short category label (``correctness``, ``security``, ``perf``,
            ``style``, ``flag``, etc.). Free-form; used for grouping in the UI.
        file: Repo-relative path of the file the finding refers to. ``file_path``
            is accepted as an alias for compatibility with the deepagents
            stdlib (``read_file``/``write_file``/``edit_file``) calling
            convention.
        title: Concise generated headline for the finding. Name the failure mode
            in roughly 4-10 words; do not copy or truncate the description.
        description: Markdown body the user sees. Do not repeat ``title`` as the
            first line.
        start_line: 1-based line in the new (post-PR) file where the
            relevant range begins. For a single-line finding, this is the
            line the issue is about. For a multi-line finding, this is the
            first line of the relevant range. Omit (with ``end_line``) for
            file-level findings.
        end_line: 1-based line where the relevant range ends. GitHub
            anchors the inline comment at ``end_line`` and renders the
            ``start_line..end_line`` span as the highlighted snippet, so
            choose ``end_line`` as the *last* line that matters — typically
            the line the comment is most directly about. For a single-line
            finding, set ``end_line == start_line`` (or omit it). Prefer
            the natural range of the issue over a single line: GitHub
            shows context above ``end_line``, so a one-line anchor often
            buries the issue under unrelated context. Defaults to
            ``start_line`` when omitted.
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
    if file is None:
        file = file_path
    if file is None:
        return {
            "success": False,
            "error": "Missing required arg `file` (or alias `file_path`).",
        }

    if start_line is not None and end_line is None:
        end_line = start_line
    if start_line is None and end_line is not None:
        start_line = end_line

    normalized_title = normalize_finding_title(title)
    if normalized_title == DEFAULT_FINDING_TITLE:
        return {"success": False, "error": "title must be a non-empty generated headline"}

    if severity not in {"low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity: {severity}"}
    if confidence not in {"low", "medium", "high"}:
        return {"success": False, "error": f"Invalid confidence: {confidence}"}
    if side not in {"LEFT", "RIGHT"}:
        return {"success": False, "error": f"Invalid side: {side}"}
    if start_line is not None and end_line is not None and end_line < start_line:
        return {"success": False, "error": "end_line must be >= start_line"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    diff_line_set = configurable.get("diff_line_set") if isinstance(configurable, dict) else None
    diff_text = configurable.get("diff_text", "") if isinstance(configurable, dict) else ""

    in_diff = not isinstance(diff_line_set, dict) or is_range_in_diff(
        diff_line_set, file, start_line, end_line, side=_cast_side(side)
    )
    if not in_diff:
        return {
            "success": False,
            "in_diff": False,
            "error": (
                "Out-of-diff findings are disabled. This finding's lines are not "
                "part of this PR's diff. Only file findings anchored to a line the "
                "PR changed. Do not re-anchor or retry."
            ),
        }

    diff_hunk: str | None = None
    if isinstance(diff_text, str) and diff_text:
        from ..reviewer_diff import extract_diff_hunk

        diff_hunk = extract_diff_hunk(diff_text, file, start_line, end_line)

    clipped_suggestion, suggestion_dropped = clip_suggestion(suggestion)

    thread_id = get_thread_id_from_runtime()
    try:
        head_sha = asyncio.run(resolve_review_head_sha(thread_id, configurable))
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    finding: Finding = new_finding(
        severity=_cast_severity(severity),
        confidence=_cast_confidence(confidence),
        category=category,
        file=file,
        start_line=start_line,
        end_line=end_line,
        description=description,
        sha=head_sha,
        title=normalized_title,
        side=_cast_side(side),
        suggestion=clipped_suggestion,
        diff_hunk=diff_hunk,
        in_diff=in_diff,
    )

    try:
        asyncio.run(append_finding(thread_id, finding))
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
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


def _cast_confidence(value: str) -> Confidence:
    return value  # type: ignore[return-value]


def _cast_side(value: str) -> DiffSide:
    return value  # type: ignore[return-value]
