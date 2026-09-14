"""Tool: ``add_finding``. Records one review finding on the reviewer thread."""

from typing import Annotated, Any

from langgraph.prebuilt import InjectedState

from agent.github.thread_token import get_github_token
from agent.review.diff import compute_diff_line_set, fetch_pr_diff, is_range_in_diff
from agent.review.findings import (
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
from agent.run_config import RunConfig


async def add_finding(
    severity: Severity,
    confidence: Confidence,
    category: str,
    file: str,
    title: str,
    description: str,
    start_line: int | None = None,
    end_line: int | None = None,
    suggestion: str | None = None,
    side: DiffSide = "RIGHT",
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Implement the `add_finding` tool."""
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

    cfg = RunConfig.from_runtime()
    diff_line_set, diff_text = await _resolve_diff_context(state, cfg)

    in_diff = not isinstance(diff_line_set, dict) or is_range_in_diff(
        diff_line_set, file, start_line, end_line, side=side
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
        from agent.review.diff import extract_diff_hunk

        diff_hunk = extract_diff_hunk(diff_text, file, start_line, end_line)

    clipped_suggestion, suggestion_dropped = clip_suggestion(suggestion)

    thread_id = get_thread_id_from_runtime()
    try:
        head_sha = await resolve_review_head_sha(thread_id, cfg)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    finding: Finding = new_finding(
        severity=severity,
        confidence=confidence,
        category=category,
        file=file,
        start_line=start_line,
        end_line=end_line,
        description=description,
        sha=head_sha,
        title=normalized_title,
        side=side,
        suggestion=clipped_suggestion,
        diff_hunk=diff_hunk,
        in_diff=in_diff,
    )

    try:
        append_result = await append_finding(thread_id, finding)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    result: dict[str, Any] = {
        "success": True,
        "finding_id": append_result["finding"]["id"],
        "duplicate": not append_result["created"],
    }
    if suggestion_dropped:
        result["suggestion_dropped"] = True
        result["warning"] = (
            f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap and was "
            "dropped — the finding was recorded with description only. Only "
            "include `suggestion` for small, obvious fixes."
        )
    return result


async def _resolve_diff_context(
    state: dict[str, Any] | None,
    cfg: RunConfig,
) -> tuple[dict[str, Any] | None, str]:
    if isinstance(state, dict):
        state_line_set = state.get("diff_line_set")
        state_diff_text = state.get("diff_text")
        if isinstance(state_line_set, dict):
            return state_line_set, state_diff_text if isinstance(state_diff_text, str) else ""
    if cfg.diff_line_set is not None:
        return cfg.diff_line_set, cfg.diff_text or ""
    token = get_github_token()
    if cfg.repo and cfg.pr_number is not None and token:
        diff_text = await fetch_pr_diff(
            owner=cfg.repo.owner,
            repo=cfg.repo.name,
            pr_number=cfg.pr_number,
            token=token,
        )
        if diff_text is not None:
            return compute_diff_line_set(diff_text), diff_text
    return None, ""
