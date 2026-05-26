"""Tool: ``publish_review``. Post the findings list to GitHub as a PR Review."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    Severity,
    filter_findings_for_publish,
    get_thread_id_from_runtime,
    get_thread_metadata,
    get_thread_slack_ref,
    replace_findings,
    set_reviewer_thread_metadata,
)
from ..reviewer_findings import (
    list_findings as list_findings_async,
)
from ..reviewer_publish import (
    fetch_pr_review_threads,
    fetch_review_comments,
    fetch_review_thread_id_for_comment,
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


def publish_review(
    severity_threshold: str = "medium",
    cap: int = 4,
) -> dict[str, Any]:
    """Post all current findings to the PR as a GitHub Review.

    Call this once at the end of a review run, after you have finished adding
    findings (and, on a re-review, after marking resolved findings via
    ``update_finding``). It will:

    1. Read findings from the reviewer thread.
    2. Filter to status=open and severity ≥ ``severity_threshold``, capped
       at ``cap`` to avoid review spam.
    3. POST a single GitHub PR Review with the eligible findings as inline
       comments. ``finding.suggestion`` becomes a ```suggestion``` block
       (the "Commit suggestion" UX). The review body is a fixed,
       host-formatted summary line — you do not write it. On a re-review
       run with no new findings to surface, the GitHub Review post is
       skipped entirely (resolved threads and ``last_reviewed_sha`` are
       still updated). The "no issues found" summary only posts on the
       first review of a PR.
    4. Store the returned per-comment IDs back on each finding so a future
       re-review can resolve those threads on GitHub when the issues are fixed.
    5. For findings whose status moved ``open`` → ``resolved`` since the last
       publish, resolve their existing GitHub review threads via the GraphQL
       ``resolveReviewThread`` mutation.
    6. Update ``last_reviewed_sha`` on the thread to the current head SHA.

    Args:
        severity_threshold: Lowest severity to surface to GitHub (default
            ``medium``). Lower-severity findings stay in state and surface in
            the future UI but not on the PR.
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
                run_id=_current_run_id(config),
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
    unpublished_findings = [
        f for f in findings if not isinstance(f.get("github_review_comment_id"), int)
    ]
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
    run_id: str | None = None,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    findings = await list_findings_async(thread_id)

    # Re-reviews only post NEW findings. Anything with a github_review_comment_id
    # already lives on GitHub from a prior publish — reposting would create
    # duplicate inline comments and break the resolve-on-fix flow (only
    # whichever duplicate id we'd cache last would resolve later).
    unpublished_findings = [
        f for f in findings if not isinstance(f.get("github_review_comment_id"), int)
    ]
    open_unpublished = [f for f in unpublished_findings if f.get("status", "open") == "open"]
    eligible = filter_findings_for_publish(
        unpublished_findings, severity_threshold=severity_threshold, cap=cap
    )

    inline_comments: list[dict[str, Any]] = []
    eligible_with_payload: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for finding in eligible:
        payload = render_inline_comment_payload(finding)
        if payload is None:
            continue
        inline_comments.append(payload)
        eligible_with_payload.append((dict(finding), payload))

    # On re-review with nothing new to surface, skip the "no issues found"
    # comment — the user already saw the previous findings, and posting
    # another summary on every push is noise. Still resolve threads for
    # findings that just moved to resolved, and advance last_reviewed_sha so
    # subsequent pushes don't redo the same diff.
    if is_re_review and not inline_comments:
        resolved_thread_count = await _resolve_threads_for_resolved_findings(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
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
    if isinstance(review_response, dict) and "_error" in review_response:
        return {
            "success": False,
            "error": f"Failed to POST PR review: {review_response['_error']}",
        }
    if review_response is None:
        # Defensive guard: with the upstream change this should never happen,
        # but keep a clear signal if it does so the agent doesn't retry blindly.
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
        if run_id is None:
            metadata = await get_thread_metadata(thread_id)
            current_run_id = metadata.get("current_reviewer_run_id")
            if isinstance(current_run_id, str) and current_run_id:
                run_id = current_run_id
        await _store_comment_ids_on_findings(
            thread_id=thread_id,
            findings=findings,
            eligible_with_payload=eligible_with_payload,
            comment_records=comment_records,
            run_id=run_id,
        )
        await _store_thread_ids_on_findings(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
        )

    resolved_thread_count = await _resolve_threads_for_resolved_findings(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
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

    return {
        "success": True,
        "review_id": review_id,
        "surfaced_count": len(inline_comments),
        "hidden_count": max(len(open_unpublished) - len(inline_comments), 0),
        "resolved_thread_count": resolved_thread_count,
    }


async def _maybe_post_slack_completion_reply(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int | None,
    surfaced_count: int,
) -> None:
    """Post a one-line completion summary to the Slack thread that started this review.

    Only fires for first reviews (gated by the caller). No-op if the reviewer
    thread has no ``slack_thread`` metadata — i.e. the review wasn't started
    from Slack.
    """
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


async def _store_comment_ids_on_findings(
    *,
    thread_id: str,
    findings: list[dict[str, Any]],
    eligible_with_payload: list[tuple[dict[str, Any], dict[str, Any]]],
    comment_records: list[dict[str, Any]],
    run_id: str | None,
) -> None:
    """Match returned GitHub comment ids back to the findings that produced them.

    Match key is ``(path, line, body)`` since we don't have a server-side hint
    pointing each REST comment to its source finding.
    """
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

    updated = False
    findings_by_id = {f.get("id"): f for f in findings}
    for finding_snapshot, payload in eligible_with_payload:
        line_value = payload.get("line")
        if not isinstance(line_value, int):
            continue
        key = (
            str(payload.get("path", "")),
            line_value,
            str(payload.get("body", "")),
        )
        comment_id = by_key.get(key)
        if comment_id is None:
            continue
        finding = findings_by_id.get(finding_snapshot.get("id"))
        if finding is None:
            continue
        finding["github_review_comment_id"] = comment_id
        if run_id:
            finding["github_review_run_id"] = run_id
        updated = True

    if updated:
        await replace_findings(thread_id, list(findings_by_id.values()))


async def _store_thread_ids_on_findings(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> None:
    findings = await list_findings_async(thread_id)
    comment_ids_by_finding_id = {
        finding.get("id"): finding.get("github_review_comment_id")
        for finding in findings
        if isinstance(finding.get("github_review_comment_id"), int)
        and not isinstance(finding.get("github_review_thread_id"), str)
    }
    if not comment_ids_by_finding_id:
        return

    threads = await fetch_pr_review_threads(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    thread_id_by_comment_id: dict[int, str] = {}
    for thread in threads:
        github_thread_id = thread.get("id")
        if not isinstance(github_thread_id, str) or not github_thread_id:
            continue
        for comment in thread.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                thread_id_by_comment_id[comment_id] = github_thread_id

    updated = False
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str):
            continue
        comment_id = comment_ids_by_finding_id.get(finding_id)
        if not isinstance(comment_id, int):
            continue
        github_thread_id = thread_id_by_comment_id.get(comment_id)
        if github_thread_id:
            finding["github_review_thread_id"] = github_thread_id
            updated = True

    if updated:
        await replace_findings(thread_id, findings)


async def _resolve_threads_for_resolved_findings(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    findings: list[dict[str, Any]],
) -> int:
    """Resolve GitHub review threads for findings that just transitioned to resolved.

    A finding qualifies if:
    - status == ``resolved``
    - has a ``github_review_comment_id`` from a prior publish
    - has not already been GitHub-resolved (tracked via
      ``github_thread_resolved`` flag we write back here)
    """
    resolved_count = 0
    mutated = False
    for finding in findings:
        if finding.get("status") != "resolved":
            continue
        comment_id = finding.get("github_review_comment_id")
        if not isinstance(comment_id, int):
            continue
        if finding.get("github_thread_resolved"):
            continue
        thread_node_id = finding.get("github_review_thread_id")
        if not isinstance(thread_node_id, str) or not thread_node_id:
            thread_node_id = await fetch_review_thread_id_for_comment(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                review_comment_id=comment_id,
                token=token,
            )
        if not thread_node_id:
            continue
        ok = await resolve_review_thread(thread_node_id=thread_node_id, token=token)
        if ok:
            finding["github_thread_resolved"] = True
            mutated = True
            resolved_count += 1

    if mutated:
        thread_id = get_thread_id_from_runtime()
        await replace_findings(thread_id, findings)

    return resolved_count


def _current_run_id(config: dict[str, Any]) -> str | None:
    candidates = [
        config.get("run_id"),
        config.get("metadata", {}).get("run_id") if isinstance(config.get("metadata"), dict) else None,
    ]
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        candidates.extend(
            [
                configurable.get("run_id"),
                configurable.get("__run_id"),
                configurable.get("langgraph_run_id"),
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
