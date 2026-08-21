"""The end-of-run review publication flow.

``agent.tools.publish_review`` is the agent-facing adapter; everything that
decides *what* gets posted and *what state that leaves behind* lives here:
which findings are still unpublished, whether an empty summary would duplicate
one already on the PR, how a rejected anchor is retried, and how the GitHub
review / comment / thread ids get stamped back onto the findings.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.config import get_config

from ..dashboard.team_settings import get_team_review_trace_links_enabled
from ..utils.dashboard_links import dashboard_review_url
from ..utils.github_checks import review_check_conclusion
from ..utils.langsmith import get_langsmith_trace_url
from ..utils.slack import post_slack_thread_reply
from ..utils.tracing import REVIEW_TRACING_PROJECT
from .diff import compute_diff_line_set, fetch_pr_diff, is_range_in_diff
from .findings import (
    REVIEWER_EVAL_PUBLICATION_KEY,
    SEVERITY_ORDER,
    TERMINAL_FINDING_STATUSES,
    Finding,
    Severity,
    comment_ids_for_finding,
    filter_findings_for_publish,
    get_thread_id_from_runtime,
    get_thread_last_reviewed_sha,
    get_thread_metadata,
    get_thread_slack_ref,
    mark_surfaced,
    replace_findings,
    resolve_review_head_sha,
    review_id_for_finding,
    set_reviewer_thread_metadata,
    thread_ids_for_finding,
)
from .findings import (
    list_findings as list_findings_async,
)
from .publish import (
    clear_review_started_comment,
    fetch_pr_review_threads,
    fetch_review_comments,
    open_swe_review_exists,
    parse_review_comment_marker,
    post_pull_request_review,
    render_inline_comment_payload,
    render_review_body,
    settle_review_check_run,
)
from .reconcile import sync_findings_with_github
from .thread_resolution import resolve_github_threads_for_finding

InlineWithPayload = list[tuple[Finding, dict[str, Any]]]

_UNRESOLVABLE_HINT = (
    "Call update_finding(status='resolved') on these ids or fix their file/line before retrying."
)


async def publish_review(
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
    trace_link_config_override: object = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post the eligible findings as one GitHub PR Review and settle run state."""
    thread_id = get_thread_id_from_runtime()
    # The run config's head_sha is frozen at run creation; a push that arrived
    # mid-run updated the live head in thread metadata. Prefer that so the
    # review anchors to (and last_reviewed_sha advances to) the commit actually
    # reviewed, not the stale one this run was created for.
    head_sha = await resolve_review_head_sha(thread_id, {"head_sha": head_sha})
    review_trace_url = await resolve_review_trace_url(thread_id, trace_link_config_override)
    review_ui_url = dashboard_review_url(owner, repo, pr_number)
    findings = await sync_findings_with_github(
        thread_id, owner=owner, repo=repo, pr_number=pr_number, token=token
    )

    selection = _select_surfaceable(
        findings,
        head_sha=head_sha,
        is_re_review=is_re_review,
        severity_threshold=severity_threshold,
        cap=cap,
    )
    eligible_with_payload = selection.with_payload
    inline_comments = [payload for _finding, payload in eligible_with_payload]

    # With nothing new to surface, skip the "no issues found" summary if Open
    # SWE has already reviewed this PR — the user already saw the previous
    # result, and posting another summary on every push is noise. We can't rely
    # on the static re_review flag alone: a push that lands mid-run is delivered
    # as a queued message into the still-running first-review run, whose
    # configurable still says re_review=False, so that path would post a
    # duplicate "No issues found". Key off the actual PR state (an existing Open
    # SWE review summary) instead. Still resolve threads for findings that just
    # moved to resolved, and advance last_reviewed_sha so subsequent pushes
    # don't redo the same diff.
    if not inline_comments and await already_reviewed(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        is_re_review=is_re_review,
    ):
        resolved_thread_count = await resolve_threads_for_resolved_findings(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
            findings=findings,
        )
        await _settle_run(thread_id, owner=owner, repo=repo, token=token, head_sha=head_sha)
        return {
            "success": True,
            "review_id": None,
            "surfaced_count": 0,
            "hidden_count": selection.open_unpublished_count,
            "resolved_thread_count": resolved_thread_count,
            "skipped_empty_re_review": True,
        }

    def _body(surfaced_count: int) -> str:
        return render_review_body(
            pr_number=pr_number,
            surfaced_count=surfaced_count,
            trace_url=review_trace_url,
            ui_url=review_ui_url,
            additional_findings_count=selection.additional_findings_count,
        )

    review_response = await post_pull_request_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        body=_body(len(inline_comments)),
        inline_comments=inline_comments,
        token=token,
    )
    unresolvable_findings: list[str] = []
    if (
        isinstance(review_response, dict)
        and review_response.get("_error_kind") == "unresolved_anchor"
    ):
        retry = await _retry_without_unresolvable_anchors(
            eligible_with_payload,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            token=token,
            state=state,
            render_body=_body,
            original_error=str(review_response.get("_error", "unknown error")),
        )
        if retry.get("_failed"):
            return {
                "success": False,
                "error": f"Failed to POST PR review: {retry['_error']}",
                "unresolvable_findings": retry["dropped_ids"],
                "hint": _UNRESOLVABLE_HINT,
            }
        review_response = retry["response"]
        inline_comments = retry["inline_comments"]
        eligible_with_payload = retry["eligible_with_payload"]
        unresolvable_findings = retry["dropped_ids"]
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
    review_id = review_response.get("id")

    if review_id is not None and inline_comments:
        await _stamp_publication_identity(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
            review_id=review_id,
            inline_with_payload=eligible_with_payload,
            langgraph_run_id=langgraph_run_id,
        )

    resolved_thread_count = await resolve_threads_for_resolved_findings(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        findings=await list_findings_async(thread_id),
    )

    if not is_re_review:
        await post_slack_completion_reply(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_id=review_id,
            surfaced_count=len(inline_comments),
        )

    await _settle_run(
        thread_id,
        owner=owner,
        repo=repo,
        token=token,
        head_sha=head_sha,
        surfaced_count=len(inline_comments),
    )

    result: dict[str, Any] = {
        "success": True,
        "review_id": review_id,
        "surfaced_count": len(inline_comments),
        "hidden_count": max(selection.open_unpublished_count - len(inline_comments), 0),
        "resolved_thread_count": resolved_thread_count,
    }
    if unresolvable_findings:
        result["unresolvable_findings"] = unresolvable_findings
        result["hint"] = (
            "Some findings had anchors not in the PR diff; "
            "call update_finding to fix or resolve them."
        )
    return result


@dataclass(frozen=True)
class _Selection:
    with_payload: InlineWithPayload
    open_unpublished_count: int
    additional_findings_count: int


def _select_surfaceable(
    findings: list[Finding],
    *,
    head_sha: str,
    is_re_review: bool,
    severity_threshold: Severity,
    cap: int,
) -> _Selection:
    """Pick the findings this run turns into inline comments.

    Re-reviews only post NEW findings: anything with a recorded review comment
    id already lives on GitHub from a prior publish, and reposting would create
    duplicate inline comments and break resolve-on-fix (only whichever duplicate
    id we cached last would resolve later). Out-of-diff findings are disabled
    outright — any legacy in-state ones just count as hidden.
    """
    unpublished = [f for f in findings if not comment_ids_for_finding(f)]
    if is_re_review:
        unpublished = [f for f in unpublished if f.get("first_seen_sha") == head_sha]
    open_unpublished = [f for f in unpublished if f.get("status", "open") == "open"]
    in_diff = [f for f in unpublished if f.get("in_diff", True)]

    eligible = filter_findings_for_publish(in_diff, severity_threshold=severity_threshold, cap=cap)
    eligible_ids = {f.get("id") for f in eligible}
    severity_rank = SEVERITY_ORDER[severity_threshold]
    below_threshold = sum(
        1
        for f in in_diff
        if f.get("id") not in eligible_ids
        and f.get("status", "open") == "open"
        and SEVERITY_ORDER.get(f.get("severity", "low"), 0) < severity_rank
    )

    return _Selection(
        with_payload=[
            (finding, payload)
            for finding in eligible
            if (payload := render_inline_comment_payload(finding)) is not None
        ],
        open_unpublished_count=len(open_unpublished),
        additional_findings_count=below_threshold,
    )


async def _stamp_publication_identity(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    review_id: int,
    inline_with_payload: InlineWithPayload,
    langgraph_run_id: str | None,
) -> None:
    """Write back where each just-published finding landed on GitHub."""
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
    await record_review_publication(
        thread_id=thread_id,
        review_id=review_id,
        inline_with_payload=inline_with_payload,
        comment_records=comment_records,
        langgraph_run_id=langgraph_run_id,
    )

    # A comment whose marker GitHub did not echo back leaves its finding without
    # an id; recover it from the PR's review threads before mapping thread ids.
    current = await list_findings_async(thread_id)
    if _missing_comment_ids_for_published_findings(current, inline_with_payload):
        await sync_findings_with_github(
            thread_id, owner=owner, repo=repo, pr_number=pr_number, token=token
        )
    await store_thread_ids_on_findings(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )


async def publish_review_dry_run(
    *,
    head_sha: str,
    severity_threshold: Severity,
    cap: int,
) -> dict[str, Any]:
    """Simulate a publish for benchmark runs, recording what would be posted."""
    thread_id = get_thread_id_from_runtime()
    findings = await list_findings_async(thread_id)
    unpublished_findings = [f for f in findings if not _has_publication_identity(f)]
    open_unpublished = [f for f in unpublished_findings if f.get("status", "open") == "open"]
    # Out-of-diff findings are disabled: only in-diff findings are surfaced.
    in_diff_unpublished = [f for f in unpublished_findings if f.get("in_diff", True)]
    eligible = filter_findings_for_publish(
        in_diff_unpublished,
        severity_threshold=severity_threshold,
        cap=cap,
    )
    finding_ids = [
        finding["id"]
        for finding in eligible
        if render_inline_comment_payload(finding) is not None and isinstance(finding.get("id"), str)
    ]

    await set_reviewer_thread_metadata(
        thread_id,
        last_reviewed_sha=head_sha,
        extra={
            REVIEWER_EVAL_PUBLICATION_KEY: {
                "finding_ids": finding_ids,
                "severity_threshold": severity_threshold,
                "cap": cap,
            }
        },
    )

    return {
        "success": True,
        "dry_run": True,
        "review_id": None,
        "surfaced_count": len(finding_ids),
        "hidden_count": max(len(open_unpublished) - len(finding_ids), 0),
        "resolved_thread_count": 0,
    }


async def already_reviewed(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    is_re_review: bool,
) -> bool:
    """Decide whether to suppress a duplicate empty "no issues found" summary.

    Suppress only when we are *certain* a prior Open SWE review exists, so a
    transient GitHub failure never causes a double-post:

    - ``is_re_review`` is a durable signal (the dispatching webhook set it from
      the persisted ``last_reviewed_sha``), so trust it outright.
    - Otherwise consult durable reviewer state (``last_reviewed_sha`` on thread
      metadata): a non-empty value means this thread already published once.
    - Only as a last resort hit the GitHub reviews API. That call is tri-state:
      ``True``/``False`` are authoritative, but ``None`` means "unknown"
      (pagination or the request failed). On ``None`` we do NOT suppress — a
      possible duplicate summary is better than silently swallowing the only
      review the user will ever see, and re-posting is the safe failure mode.
    """
    if is_re_review:
        return True
    metadata = await get_thread_metadata(thread_id)
    if get_thread_last_reviewed_sha(metadata):
        return True
    exists = await open_swe_review_exists(owner=owner, repo=repo, pr_number=pr_number, token=token)
    return exists is True


async def record_review_publication(
    *,
    thread_id: str,
    review_id: int,
    inline_with_payload: InlineWithPayload,
    comment_records: list[dict[str, Any]],
    langgraph_run_id: str | None,
) -> None:
    """Stamp the review id and inline comment ids onto findings in one write.

    Collapsing the review-id and comment-id updates into a single
    read-modify-write keeps publication identity atomic: a finding is never
    persisted carrying a review id without also carrying whatever comment id
    GitHub returned for it in the same record.
    """
    review_finding_ids = {
        finding_id
        for finding, _payload in inline_with_payload
        if isinstance(finding_id := finding.get("id"), str)
    }
    comment_id_by_finding_id = _comment_id_by_finding_id(inline_with_payload, comment_records)

    latest = await list_findings_async(thread_id)
    changed = _apply_review_id(latest, finding_ids=review_finding_ids, review_id=review_id)
    changed = (
        _apply_comment_ids(
            latest,
            comment_id_by_finding_id=comment_id_by_finding_id,
            langgraph_run_id=langgraph_run_id,
        )
        or changed
    )
    if changed:
        await replace_findings(thread_id, latest)


async def store_thread_ids_on_findings(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> None:
    """Attach the GitHub review thread id to every finding that lacks one."""
    findings = await list_findings_async(thread_id)
    comment_ids_by_finding_id: dict[str, list[int]] = {}
    for finding in findings:
        finding_id = finding.get("id")
        comment_ids = comment_ids_for_finding(finding)
        if isinstance(finding_id, str) and comment_ids and not thread_ids_for_finding(finding):
            comment_ids_by_finding_id[finding_id] = comment_ids
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
        thread_ids = thread_ids_for_finding(finding)
        for comment_id in comment_ids_by_finding_id.get(finding_id, []):
            github_thread_id = thread_id_by_comment_id.get(comment_id)
            if not github_thread_id or github_thread_id in thread_ids:
                continue
            thread_ids.append(github_thread_id)
            finding["github_review_thread_ids"] = thread_ids
            mark_surfaced(finding)
            updated = True

    if updated:
        await replace_findings(thread_id, findings)


async def resolve_threads_for_resolved_findings(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    findings: list[Finding],
) -> int:
    """Resolve the GitHub threads of every finding this run marked terminal."""
    resolved_count = 0
    mutated = False
    for finding in findings:
        if finding.get("status") not in TERMINAL_FINDING_STATUSES:
            continue
        resolution = await resolve_github_threads_for_finding(
            finding,
            status=str(finding.get("status")),
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
        )
        finding.update(resolution.fields)  # type: ignore[typeddict-item]
        resolved_count += resolution.resolved_count
        mutated = mutated or resolution.changed

    if mutated:
        await replace_findings(get_thread_id_from_runtime(), findings)
    return resolved_count


async def post_slack_completion_reply(
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
    channel_id = slack_ref.get("channel_id")
    thread_ts = slack_ref.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return

    if surfaced_count == 0:
        headline = "*Open SWE Review*: No issues found."
    else:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        headline = f"*Open SWE Review* found {surfaced_count} potential {issue_word}."

    review_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    if isinstance(review_id, int):
        review_url = f"{review_url}#pullrequestreview-{review_id}"

    await post_slack_thread_reply(
        channel_id, thread_ts, f"{headline} <{review_url}|View review>", agent_thread_id=thread_id
    )


async def resolve_diff_context(
    *,
    state: Mapping[str, Any] | None,
    configurable: Mapping[str, Any] | None,
    owner: str | None,
    repo: str | None,
    pr_number: int | None,
    token: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """The PR's ``(diff_line_set, diff_text)`` for this reviewer run.

    Reviewer runs clear ``diff_line_set`` from the run config before the agent
    starts (so ``add_finding`` trusts the agent's anchors), which means neither
    the anchor check nor the publish-time retry can rely on it being there:
    prefer injected state, then the config, then fetch the PR's unified diff and
    recompute. ``(None, "")`` means the diff is simply unknown.
    """
    for source in (state, configurable):
        if isinstance(source, Mapping):
            line_set = source.get("diff_line_set")
            if isinstance(line_set, dict):
                diff_text = source.get("diff_text")
                return line_set, diff_text if isinstance(diff_text, str) else ""
    if not owner or not repo or not isinstance(pr_number, int) or not token:
        return None, ""
    diff_text = await fetch_pr_diff(owner=owner, repo=repo, pr_number=pr_number, token=token)
    if diff_text is None:
        return None, ""
    return compute_diff_line_set(diff_text), diff_text


async def resolve_diff_line_set(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    config = get_config()
    configurable = config.get("configurable") if isinstance(config, dict) else None
    line_set, _diff_text = await resolve_diff_context(
        state=state,
        configurable=configurable if isinstance(configurable, Mapping) else None,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    return line_set


async def filter_against_pr_diff(
    eligible_with_payload: InlineWithPayload,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    state: dict[str, Any] | None = None,
) -> tuple[InlineWithPayload, list[str]]:
    """Drop findings whose path/line range is not in the current PR diff.

    Returns ``(valid_with_payload, dropped_finding_ids)``. When the diff
    cannot be resolved (fetch failed and no cached set), we return everything
    unchanged and an empty drop list — the caller will then surface the
    original error rather than retry blindly.
    """
    diff_line_set = await resolve_diff_line_set(
        owner=owner, repo=repo, pr_number=pr_number, token=token, state=state
    )
    if diff_line_set is None:
        return list(eligible_with_payload), []

    valid: InlineWithPayload = []
    dropped: list[str] = []
    for finding, payload in eligible_with_payload:
        path = payload.get("path")
        # Prefer the finding's recorded range; fall back to the payload line.
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


async def resolve_review_trace_url(thread_id: str, config_override: object) -> str | None:
    if config_override is False:
        return None
    if not await get_team_review_trace_links_enabled():
        return None
    if not thread_id:
        return None
    return await get_langsmith_trace_url(thread_id, project_name=REVIEW_TRACING_PROJECT)


async def _retry_without_unresolvable_anchors(
    eligible_with_payload: InlineWithPayload,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    token: str,
    state: dict[str, Any] | None,
    render_body: Callable[[int], str],
    original_error: str,
) -> dict[str, Any]:
    """Re-POST the review with only the findings GitHub can anchor.

    GitHub rejects the whole batch when any inline comment points outside the
    PR diff. Returning the bare 422 to the agent only invites it to retry
    ``publish_review`` with byte-identical args until findings drain, so drop
    exactly the bad anchors and try once more.
    """
    valid_with_payload, dropped_ids = await filter_against_pr_diff(
        eligible_with_payload,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        state=state,
    )
    if not dropped_ids or not valid_with_payload:
        # Either nothing to drop (no diff_line_set available, so we can't tell
        # which findings are bad) or everything would be dropped. Either way, do
        # not retry — surface the structural signal so the agent stops retrying
        # with the same args.
        return {"_failed": True, "_error": original_error, "dropped_ids": dropped_ids}

    retry_inline = [payload for _finding, payload in valid_with_payload]
    retry_response = await post_pull_request_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        body=render_body(len(retry_inline)),
        inline_comments=retry_inline,
        token=token,
    )
    if not isinstance(retry_response, dict) or "_error" in retry_response:
        retry_error = (
            retry_response.get("_error", "unknown error")
            if isinstance(retry_response, dict)
            else "no response"
        )
        return {"_failed": True, "_error": retry_error, "dropped_ids": dropped_ids}
    return {
        "response": retry_response,
        "inline_comments": retry_inline,
        "eligible_with_payload": valid_with_payload,
        "dropped_ids": dropped_ids,
    }


async def _settle_run(
    thread_id: str,
    *,
    owner: str,
    repo: str,
    token: str,
    head_sha: str,
    surfaced_count: int = 0,
) -> None:
    await set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)
    await clear_review_started_comment(thread_id=thread_id, owner=owner, repo=repo, token=token)
    conclusion, check_title, check_summary = review_check_conclusion(surfaced_count)
    await settle_review_check_run(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        token=token,
        conclusion=conclusion,
        title=check_title,
        summary=check_summary,
    )


def _has_publication_identity(finding: Finding) -> bool:
    return bool(comment_ids_for_finding(finding)) or review_id_for_finding(finding) is not None


def _missing_comment_ids_for_published_findings(
    findings: list[Finding],
    eligible_with_payload: InlineWithPayload,
) -> bool:
    finding_ids = {
        finding_id
        for finding, _payload in eligible_with_payload
        if isinstance(finding_id := finding.get("id"), str)
    }
    return any(
        finding.get("id") in finding_ids and not comment_ids_for_finding(finding)
        for finding in findings
    )


def _apply_review_id(
    findings: list[Finding],
    *,
    finding_ids: set[str],
    review_id: int,
) -> bool:
    updated = False
    for finding in findings:
        if finding.get("id") in finding_ids and review_id_for_finding(finding) != review_id:
            finding["github_review_id"] = review_id
            mark_surfaced(finding)
            updated = True
    return updated


def _apply_comment_ids(
    findings: list[Finding],
    *,
    comment_id_by_finding_id: dict[str, int],
    langgraph_run_id: str | None,
) -> bool:
    updated = False
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str):
            continue
        comment_id = comment_id_by_finding_id.get(finding_id)
        if comment_id is None:
            continue
        comment_ids = comment_ids_for_finding(finding)
        if comment_id not in comment_ids:
            comment_ids.append(comment_id)
            finding["github_review_comment_ids"] = comment_ids
        mark_surfaced(finding)
        if langgraph_run_id:
            finding["github_review_run_id"] = langgraph_run_id
        updated = True
    return updated


def _comment_id_by_finding_id(
    eligible_with_payload: InlineWithPayload,
    comment_records: list[dict[str, Any]],
) -> dict[str, int]:
    """Map each surfaced finding id to its GitHub comment id via the marker.

    The embedded Open SWE marker is the *only* source of truth. Every comment
    this reviewer posts carries a ``<!-- open-swe-review-comment {...} -->``
    marker keyed by finding id (see ``render_inline_comment_body``), so the
    match is exact. The old ``(path, line, body)`` fallback collided whenever
    two findings shared a path/line/body — it cached the same comment id on
    both, which corrupts resolve-on-fix (resolving one would target the wrong
    thread). Findings whose comment lacks a parseable marker are left out here;
    ``sync_findings_with_github`` recovers them via the same marker against the
    PR's review threads.
    """
    by_marker_id: dict[str, int] = {}
    for record in comment_records:
        body = record.get("body", "")
        comment_id = record.get("id")
        if isinstance(body, str) and isinstance(comment_id, int):
            marker = parse_review_comment_marker(body)
            if marker is not None:
                by_marker_id[marker["id"]] = comment_id

    out: dict[str, int] = {}
    for finding_snapshot, _payload in eligible_with_payload:
        finding_id = finding_snapshot.get("id")
        if isinstance(finding_id, str) and finding_id in by_marker_id:
            out[finding_id] = by_marker_id[finding_id]
    return out
