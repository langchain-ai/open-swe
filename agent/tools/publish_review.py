"""Tool: ``publish_review``. Post the findings list to GitHub as a PR Review.

The GitHub PR is the source of truth for review state. ``publish_review``
does three things, in order:

1. Posts a single GitHub PR Review containing inline comments for findings
   that originated in the current run (no ``github_review_comment_id``).
   On a re-review with no new findings, the post is skipped — the user has
   already seen the previous review.
2. Resolves the GitHub review threads for findings now marked
   ``resolved`` / ``dismissed``. Findings reconstructed from PR threads
   carry the thread node ids on ``github_review_thread_ids`` directly, so
   no reconciliation lookup is needed.
3. Records ``{comment_id: {run_id, finding_id}}`` entries on the reviewer
   thread's ``published_comments`` map so the GitHub-reaction →
   LangSmith-feedback flow can find the originating LangGraph run later
   without scanning findings.

After publish, ``last_reviewed_sha`` is advanced so the next push webhook
knows what diff to re-review against.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..reviewer_diff import compute_diff_line_set, fetch_pr_diff, is_range_in_diff
from ..reviewer_findings import (
    Finding,
    Severity,
    filter_findings_for_publish,
    get_thread_id_from_runtime,
    get_thread_metadata,
    get_thread_slack_ref,
    record_published_comments,
    set_reviewer_thread_metadata,
)
from ..reviewer_findings import (
    list_findings as list_findings_async,
)
from ..reviewer_publish import (
    fetch_review_comments,
    parse_review_comment_marker,
    post_pull_request_review,
    render_inline_comment_payload,
    render_review_body,
    resolve_review_thread,
)
from ..utils.github_token import (
    GitHubAuthError,
    get_github_token,
    invalidate_cached_github_token,
)
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)


def publish_review(
    severity_threshold: str = "medium",
    cap: int = 4,
) -> dict[str, Any]:
    """Post all current findings to the PR as a GitHub Review.

    Call this once at the end of a review run, after you have finished adding
    findings (and, on a re-review, after marking resolved findings via
    ``update_finding``).

    Args:
        severity_threshold: Lowest severity to surface to GitHub (default
            ``medium``). Lower-severity findings stay in state but are not
            posted.
        cap: Maximum number of inline comments to publish (default 4).

    Returns:
        Dictionary with ``success``, ``review_id``, ``surfaced_count``,
        ``hidden_count``, ``resolved_thread_count``.
    """
    if severity_threshold not in {"low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity_threshold: {severity_threshold}"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    head_sha = configurable.get("head_sha") if isinstance(configurable, dict) else None
    is_re_review = bool(configurable.get("re_review")) if isinstance(configurable, dict) else False

    if (
        not isinstance(repo_config, dict)
        or not repo_config.get("owner")
        or not repo_config.get("name")
    ):
        return {"success": False, "error": "Missing repo info in run config"}
    if not isinstance(pr_number, int):
        return {"success": False, "error": "Missing pr_number in run config"}
    if not isinstance(head_sha, str) or not head_sha:
        return {"success": False, "error": "Missing head_sha in run config"}

    if _is_reviewer_eval_mode(configurable):
        return asyncio.run(
            _publish_review_eval_dry_run_async(
                head_sha=head_sha,
                severity_threshold=_cast_severity(severity_threshold),
                cap=cap,
            )
        )

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    try:
        return asyncio.run(
            _publish_review_async(
                owner=str(repo_config["owner"]),
                repo=str(repo_config["name"]),
                pr_number=pr_number,
                head_sha=head_sha,
                token=token,
                severity_threshold=_cast_severity(severity_threshold),
                cap=cap,
                is_re_review=is_re_review,
                langgraph_run_id=_current_run_id(config),
            )
        )
    except GitHubAuthError as exc:
        thread_id = get_thread_id_from_runtime()
        if thread_id:
            asyncio.run(invalidate_cached_github_token(thread_id))
        return {
            "success": False,
            "error": (
                "GitHub returned 401 — the cached OAuth token is invalid or revoked. "
                "Please re-authenticate and trigger the review again."
            ),
            "auth_error": str(exc),
        }


def _cast_severity(value: str) -> Severity:
    return value  # type: ignore[return-value]


def _is_reviewer_eval_mode(configurable: dict[str, Any]) -> bool:
    return configurable.get("reviewer_eval") is True or configurable.get("eval") is True


async def _publish_review_eval_dry_run_async(
    *,
    head_sha: str,
    severity_threshold: Severity,
    cap: int,
) -> dict[str, Any]:
    """Simulate publish_review for benchmark runs without posting to GitHub."""
    thread_id = get_thread_id_from_runtime()
    findings = await list_findings_async(thread_id)
    unpublished_findings = [f for f in findings if not _has_publication_identity(f)]
    open_unpublished = [f for f in unpublished_findings if f.get("status", "open") == "open"]
    eligible = filter_findings_for_publish(
        unpublished_findings,
        severity_threshold=severity_threshold,
        cap=cap,
    )
    inline_comments = [
        payload
        for finding in eligible
        if (payload := render_inline_comment_payload(finding)) is not None
    ]

    await set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)

    return {
        "success": True,
        "dry_run": True,
        "review_id": None,
        "surfaced_count": len(inline_comments),
        "hidden_count": max(len(open_unpublished) - len(inline_comments), 0),
        "resolved_thread_count": 0,
    }


async def _publish_review_async(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    token: str,
    severity_threshold: Severity,
    cap: int,
    is_re_review: bool,
    langgraph_run_id: str | None = None,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    findings = await list_findings_async(thread_id)

    unpublished_findings = [f for f in findings if not _has_publication_identity(f)]
    open_unpublished = [f for f in unpublished_findings if f.get("status", "open") == "open"]
    eligible = filter_findings_for_publish(
        unpublished_findings, severity_threshold=severity_threshold, cap=cap
    )

    inline_comments: list[dict[str, Any]] = []
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]] = []
    for finding in eligible:
        payload = render_inline_comment_payload(finding)
        if payload is None:
            continue
        inline_comments.append(payload)
        eligible_with_payload.append((finding, payload))

    # On re-review with nothing new to surface, skip the "no issues found"
    # comment — the user already saw the previous findings. Still resolve
    # threads for newly-resolved findings and advance last_reviewed_sha.
    if is_re_review and not inline_comments:
        resolved_thread_count = await _resolve_threads_for_resolved_findings(
            token=token,
            findings=findings,
        )
        await set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)
        return {
            "success": True,
            "review_id": None,
            "surfaced_count": 0,
            "hidden_count": max(len(open_unpublished), 0),
            "resolved_thread_count": resolved_thread_count,
            "skipped_empty_re_review": True,
        }

    review_body = render_review_body(
        pr_number=pr_number,
        surfaced_count=len(inline_comments),
    )

    review_response = await post_pull_request_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        body=review_body,
        inline_comments=inline_comments,
        token=token,
    )
    # If GitHub rejected the batch because one or more inline comments anchor
    # to a file/line that's not in the PR diff, drop just those findings and
    # retry once. Returning the bare 422 to the agent only invites it to
    # retry publish_review with byte-identical args until findings drain.
    unresolvable_findings: list[str] = []
    if (
        isinstance(review_response, dict)
        and review_response.get("_error_kind") == "unresolved_anchor"
    ):
        valid_with_payload, dropped_ids = await _filter_against_pr_diff(
            eligible_with_payload,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
        )
        if dropped_ids and valid_with_payload:
            retry_inline = [p for _, p in valid_with_payload]
            retry_body = render_review_body(pr_number=pr_number, surfaced_count=len(retry_inline))
            retry_response = await post_pull_request_review(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                body=retry_body,
                inline_comments=retry_inline,
                token=token,
            )
            if isinstance(retry_response, dict) and "_error" not in retry_response:
                review_response = retry_response
                inline_comments = retry_inline
                eligible_with_payload = valid_with_payload
                unresolvable_findings = dropped_ids
            else:
                retry_error = (
                    retry_response.get("_error", "unknown error")
                    if isinstance(retry_response, dict)
                    else "no response"
                )
                return {
                    "success": False,
                    "error": f"Failed to POST PR review: {retry_error}",
                    "unresolvable_findings": dropped_ids,
                    "hint": (
                        "Call update_finding(status='resolved') on these ids "
                        "or fix their file/line before retrying."
                    ),
                }
        else:
            return {
                "success": False,
                "error": f"Failed to POST PR review: {review_response['_error']}",
                "unresolvable_findings": dropped_ids,
                "hint": (
                    "Call update_finding(status='resolved') on these ids "
                    "or fix their file/line before retrying."
                ),
            }
    if isinstance(review_response, dict) and "_error" in review_response:
        return {
            "success": False,
            "error": f"Failed to POST PR review: {review_response['_error']}",
        }
    if review_response is None:
        return {
            "success": False,
            "error": "Failed to POST PR review: no response from GitHub",
        }
    review_id = review_response.get("id") if isinstance(review_response, dict) else None

    if review_id is not None and inline_comments:
        comment_records = await fetch_review_comments(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_id=review_id,
            token=token,
        )
        if langgraph_run_id is None:
            metadata = await get_thread_metadata(thread_id)
            current_run_id = metadata.get("current_reviewer_run_id")
            if isinstance(current_run_id, str) and current_run_id:
                langgraph_run_id = current_run_id
        await _record_published_findings(
            thread_id=thread_id,
            findings=findings,
            eligible_with_payload=eligible_with_payload,
            comment_records=comment_records,
            langgraph_run_id=langgraph_run_id,
        )

    resolved_thread_count = await _resolve_threads_for_resolved_findings(
        token=token,
        findings=await list_findings_async(thread_id),
    )

    if not is_re_review:
        await _maybe_post_slack_completion_reply(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_id=review_id,
            surfaced_count=len(inline_comments),
        )

    await set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)

    result: dict[str, Any] = {
        "success": True,
        "review_id": review_id,
        "surfaced_count": len(inline_comments),
        "hidden_count": max(len(open_unpublished) - len(inline_comments), 0),
        "resolved_thread_count": resolved_thread_count,
    }
    if unresolvable_findings:
        result["unresolvable_findings"] = unresolvable_findings
        result["hint"] = (
            "Some findings had anchors not in the PR diff; "
            "call update_finding to fix or resolve them."
        )
    return result


def _has_publication_identity(finding: Finding) -> bool:
    return isinstance(finding.get("github_review_comment_id"), int)


async def _resolve_diff_line_set(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> dict[str, set[int]] | None:
    """Return the new-side line set for the PR diff, fetching it if needed.

    Reviewer runs clear ``configurable['diff_line_set']`` before the agent
    starts (so ``add_finding`` trusts the agent's anchors), which means the
    publish-time retry path can't rely on it being populated. Fetch the PR's
    unified diff from the GitHub REST API and recompute the line set on the
    fly. Returns ``None`` if the fetch fails — caller treats that as "we
    can't tell which finding is bad, don't retry blindly".
    """
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    cached = configurable.get("diff_line_set") if isinstance(configurable, dict) else None
    if isinstance(cached, dict):
        return cached

    diff_text = await fetch_pr_diff(owner=owner, repo=repo, pr_number=pr_number, token=token)
    if diff_text is None:
        return None
    return compute_diff_line_set(diff_text)


async def _filter_against_pr_diff(
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]],
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> tuple[list[tuple[Finding, dict[str, Any]]], list[str]]:
    """Drop findings whose path/line range is not in the current PR diff.

    Returns ``(valid_with_payload, dropped_finding_ids)``. When the diff
    cannot be resolved (fetch failed and no cached set), we return everything
    unchanged and an empty drop list — the caller will then surface the
    original error rather than retry blindly.
    """
    diff_line_set = await _resolve_diff_line_set(
        owner=owner, repo=repo, pr_number=pr_number, token=token
    )
    if diff_line_set is None:
        return list(eligible_with_payload), []

    valid: list[tuple[Finding, dict[str, Any]]] = []
    dropped: list[str] = []
    for finding, payload in eligible_with_payload:
        path = payload.get("path")
        start_line = finding.get("start_line")
        end_line = finding.get("end_line")
        if end_line is None:
            payload_line = payload.get("line")
            if isinstance(payload_line, int):
                end_line = payload_line
                if start_line is None:
                    start_line = payload_line
        side = finding.get("side") if finding.get("side") in {"LEFT", "RIGHT"} else "RIGHT"
        if isinstance(path, str) and is_range_in_diff(
            diff_line_set, path, start_line, end_line, side=side
        ):
            valid.append((finding, payload))
        else:
            finding_id = finding.get("id")
            if isinstance(finding_id, str):
                dropped.append(finding_id)
    return valid, dropped


async def _maybe_post_slack_completion_reply(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int | None,
    surfaced_count: int,
) -> None:
    """Post a one-line completion summary to the Slack thread that started this review."""
    metadata = await get_thread_metadata(thread_id)
    slack_ref = get_thread_slack_ref(metadata)
    if slack_ref is None:
        return

    if surfaced_count == 0:
        headline = "*Open SWE Review*: No issues found."
    else:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        headline = f"*Open SWE Review* found {surfaced_count} potential {issue_word}."

    review_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    if isinstance(review_id, int):
        review_url = f"{review_url}#pullrequestreview-{review_id}"
    text = f"{headline} <{review_url}|View review>"

    await post_slack_thread_reply(slack_ref["channel_id"], slack_ref["thread_ts"], text)


async def _record_published_findings(
    *,
    thread_id: str,
    findings: list[Finding],
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]],
    comment_records: list[dict[str, Any]],
    langgraph_run_id: str | None,
) -> None:
    """Stamp each posted finding with its GitHub ``comment_id`` and record
    the comment in the cross-run ``published_comments`` map.

    Match strategy: prefer the open-swe marker (survives body reformatting)
    and fall back to ``(path, line, body)`` when the marker is missing.
    """
    by_marker_id: dict[str, int] = {}
    by_key: dict[tuple[str, int, str], int] = {}
    for record in comment_records:
        path = record.get("path")
        line = record.get("line") or record.get("original_line")
        body = record.get("body", "")
        comment_id = record.get("id")
        if (
            isinstance(path, str)
            and isinstance(line, int)
            and isinstance(body, str)
            and isinstance(comment_id, int)
        ):
            by_key[(path, line, body)] = comment_id
            marker = parse_review_comment_marker(body)
            if marker is not None:
                by_marker_id[marker["id"]] = comment_id

    new_published: dict[int, dict[str, str]] = {}
    findings_by_id = {f.get("id"): f for f in findings}
    updated = False
    for finding_snapshot, payload in eligible_with_payload:
        finding_id = finding_snapshot.get("id")
        comment_id = by_marker_id.get(finding_id) if isinstance(finding_id, str) else None
        line_value = payload.get("line")
        if comment_id is None and isinstance(line_value, int):
            key = (
                str(payload.get("path", "")),
                line_value,
                str(payload.get("body", "")),
            )
            comment_id = by_key.get(key)
        if comment_id is None or not isinstance(finding_id, str):
            continue
        finding = findings_by_id.get(finding_id)
        if finding is None:
            continue
        finding["github_review_comment_id"] = comment_id
        updated = True
        if langgraph_run_id:
            new_published[comment_id] = {
                "run_id": langgraph_run_id,
                "finding_id": finding_id,
            }

    if updated:
        from ..reviewer_findings import replace_findings

        await replace_findings(thread_id, list(findings_by_id.values()))
    if new_published:
        await record_published_comments(thread_id, new_published)


async def _resolve_threads_for_resolved_findings(
    *,
    token: str,
    findings: list[Finding],
) -> int:
    """Resolve GitHub review threads for findings now marked resolved/dismissed.

    The thread ids live on the finding (set when it was reconstructed from
    PR threads at run start), so no lookup is required.
    """
    resolved_count = 0
    for finding in findings:
        if finding.get("status") not in {"resolved", "dismissed"}:
            continue
        for thread_node_id in finding.get("github_review_thread_ids") or []:
            if not isinstance(thread_node_id, str) or not thread_node_id:
                continue
            ok = await resolve_review_thread(thread_node_id=thread_node_id, token=token)
            if ok:
                resolved_count += 1
    return resolved_count


def _current_run_id(config: dict[str, Any]) -> str | None:
    candidates = [config.get("run_id")]
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        candidates.append(configurable.get("run_id"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
