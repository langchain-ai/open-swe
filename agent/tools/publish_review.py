"""Tool: ``publish_review``. Post the findings list to GitHub as a PR Review."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config, get_store
from langgraph.prebuilt import InjectedState

from ..dashboard.agent_overrides import load_profile
from ..dashboard.autofix_state import (
    get_pr_autofix_cycle_count,
    is_pr_autofix_disabled,
    set_pr_autofix_cycle_count,
)
from ..dashboard.enabled_repos import is_review_repo_enabled
from ..dashboard.team_settings import (
    get_team_autofix_settings,
    get_team_review_trace_links_enabled,
)
from ..dispatch import dispatch_agent_run, dispatch_client
from ..review.diff import compute_diff_line_set, fetch_pr_diff, is_range_in_diff
from ..review.findings import (
    DEFAULT_FINDING_TITLE,
    REVIEW_FINDING_CAP,
    REVIEWER_EVAL_PUBLICATION_KEY,
    REVIEWER_THREAD_KIND,
    SEVERITY_ORDER,
    Finding,
    ReviewerThreadMissingError,
    Severity,
    _coerce_surface,
    filter_findings_for_publish,
    get_thread_id_from_runtime,
    get_thread_last_reviewed_sha,
    get_thread_metadata,
    get_thread_slack_ref,
    replace_findings,
    resolve_review_head_sha,
    set_reviewer_thread_metadata,
    thread_missing_tool_result,
)
from ..review.findings import (
    list_findings as list_findings_async,
)
from ..review.publish import (
    clear_review_started_comment,
    fetch_pr_review_threads,
    fetch_review_comments,
    fetch_review_thread_id_for_comment,
    open_swe_review_exists,
    parse_review_comment_marker,
    post_pull_request_review,
    render_inline_comment_payload,
    render_resolution_comment,
    render_review_body,
    reply_to_review_comment,
    resolve_review_thread,
    settle_review_check_run,
)
from ..review.reconcile import reconcile_findings_with_review_threads
from ..utils.dashboard_links import dashboard_review_url, dashboard_thread_url
from ..utils.github_checks import (
    post_autofix_status_check,
    review_check_blocking_enabled,
    review_check_conclusion,
)
from ..utils.github_comments import get_thread_id_from_branch
from ..utils.github_token import (
    GitHubAuthError,
    get_github_token,
    invalidate_cached_github_token,
)
from ..utils.langsmith import get_langsmith_trace_url
from ..utils.slack import post_slack_thread_reply
from ..utils.tracing import REVIEW_TRACING_PROJECT

logger = logging.getLogger(__name__)

_AUTOFIX_MAX_CYCLES = 2
_AUTOFIX_NUDGE = "Process the pending Open SWE review auto-fix event for this pull request."


async def publish_review(
    severity_threshold: str = "medium",
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Post all current findings to the PR as a GitHub Review.

    Call this once at the end of a review run, after you have finished adding
    findings (and, on a re-review, after marking resolved findings via
    ``update_finding``). The tool posts one GitHub PR Review for eligible
    inline findings, records the GitHub comment/thread IDs for future
    re-reviews, resolves GitHub threads for findings now marked resolved, and
    advances the reviewer thread's ``last_reviewed_sha``.

    On a re-review with no new findings to surface, it skips posting a new
    GitHub Review but still resolves fixed threads and updates reviewer state.

    Args:
        severity_threshold: Lowest severity to surface as inline GitHub comments
            (default ``medium``). Lower-severity findings stay in state and are
            mentioned in the review summary with a link to the web app, but are
            not posted as inline PR comments.
    Returns:
        Dictionary with ``success``, ``review_id``, ``surfaced_count``,
        ``hidden_count``, ``resolved_thread_count``, and sometimes
        ``unresolvable_findings``, plus the flags below.

        ``success: true`` alone does NOT mean a GitHub Review was posted —
        check the flags:

        - ``skipped_empty_re_review: true`` (with ``review_id: null``): an
          empty re-review was deliberately skipped. No GitHub Review was
          created; the call was a valid no-op. Do not describe the review as
          published/posted/submitted.
        - ``dry_run: true`` (with ``review_id: null``): eval/benchmark mode —
          the publish was simulated and nothing was posted to GitHub. Do not
          claim publication.

        Only a numeric ``review_id`` (with neither flag set) confirms a real
        GitHub Review was created.
    """
    if severity_threshold not in {"low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity_threshold: {severity_threshold}"}

    config = get_config()
    raw_configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    repo_config = configurable.get("repo")
    pr_number = configurable.get("pr_number")
    head_sha = configurable.get("head_sha")
    is_re_review = bool(configurable.get("re_review"))
    raw_branch_name = configurable.get("branch_name")
    branch_name = raw_branch_name if isinstance(raw_branch_name, str) else ""

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
        eval_threshold = configurable.get("reviewer_eval_severity_threshold")
        if isinstance(eval_threshold, str) and eval_threshold in {
            "low",
            "medium",
            "high",
            "critical",
        }:
            severity_threshold = eval_threshold
        eval_cap = configurable.get("reviewer_eval_cap")
        if not isinstance(eval_cap, int) or isinstance(eval_cap, bool) or eval_cap < 0:
            eval_cap = REVIEW_FINDING_CAP
        try:
            return await _publish_review_eval_dry_run_async(
                head_sha=head_sha,
                severity_threshold=_cast_severity(severity_threshold),
                cap=eval_cap,
            )
        except ReviewerThreadMissingError as exc:
            return thread_missing_tool_result(exc)

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    try:
        return await _publish_review_async(
            owner=str(repo_config["owner"]),
            repo=str(repo_config["name"]),
            pr_number=pr_number,
            head_sha=head_sha,
            token=token,
            severity_threshold=_cast_severity(severity_threshold),
            cap=REVIEW_FINDING_CAP,
            is_re_review=is_re_review,
            branch_name=branch_name,
            langgraph_run_id=_current_run_id(config),
            trace_link_config_override=configurable.get("review_trace_link_enabled"),
            state=state,
        )
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    except GitHubAuthError as exc:
        thread_id = get_thread_id_from_runtime()
        if thread_id:
            await invalidate_cached_github_token(thread_id)
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


async def _resolve_review_trace_url(thread_id: str, config_override: object) -> str | None:
    if config_override is False:
        return None
    if not await get_team_review_trace_links_enabled():
        return None
    if not thread_id:
        return None
    return get_langsmith_trace_url(thread_id, project_name=REVIEW_TRACING_PROJECT)


def _is_reviewer_eval_mode(configurable: dict[str, Any]) -> bool:
    return configurable.get("reviewer_eval") is True or configurable.get("eval") is True


async def _review_check_finding_count(thread_id: str, newly_posted_count: int) -> int:
    # Blocking checks count all standing findings; informational checks preserve publish-time count.
    if not review_check_blocking_enabled():
        return newly_posted_count
    findings = await list_findings_async(thread_id)
    return sum(
        1
        for finding in findings
        if finding.get("status", "open") == "open"
        and isinstance((surface := finding.get("surface")), dict)
        and surface.get("state") in {"surfaced", "resolve_pending"}
    )


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
    # Out-of-diff findings are disabled: only in-diff findings are surfaced.
    in_diff_unpublished = [f for f in unpublished_findings if f.get("in_diff", True)]
    eligible = filter_findings_for_publish(
        in_diff_unpublished,
        severity_threshold=severity_threshold,
        cap=cap,
    )
    eligible_with_payload = [
        (finding, payload)
        for finding in eligible
        if (payload := render_inline_comment_payload(finding)) is not None
    ]
    finding_ids = [
        finding["id"]
        for finding, _payload in eligible_with_payload
        if isinstance(finding.get("id"), str)
    ]
    publication = {
        "finding_ids": finding_ids,
        "severity_threshold": severity_threshold,
        "cap": cap,
    }

    await set_reviewer_thread_metadata(
        thread_id,
        last_reviewed_sha=head_sha,
        extra={REVIEWER_EVAL_PUBLICATION_KEY: publication},
    )

    return {
        "success": True,
        "dry_run": True,
        "review_id": None,
        "surfaced_count": len(eligible_with_payload),
        "hidden_count": max(len(open_unpublished) - len(eligible_with_payload), 0),
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
    branch_name: str = "",
    langgraph_run_id: str | None = None,
    trace_link_config_override: object = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    # The run config's head_sha is frozen at run creation; a push that arrived
    # mid-run updated the live head in thread metadata. Prefer that so the
    # review anchors to (and last_reviewed_sha advances to) the commit actually
    # reviewed, not the stale one this run was created for.
    head_sha = await resolve_review_head_sha(thread_id, {"head_sha": head_sha})
    review_trace_url = await _resolve_review_trace_url(thread_id, trace_link_config_override)
    review_ui_url = dashboard_review_url(owner, repo, pr_number)
    findings = await _backfill_findings_from_pr_threads(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )

    # Re-reviews only post NEW findings. Anything with a github_review_comment_id
    # already lives on GitHub from a prior publish — reposting would create
    # duplicate inline comments and break the resolve-on-fix flow (only
    # whichever duplicate id we'd cache last would resolve later).
    unpublished_findings = [
        f for f in findings if not isinstance(f.get("github_review_comment_id"), int)
    ]
    if is_re_review:
        unpublished_findings = [
            f for f in unpublished_findings if f.get("first_seen_sha") == head_sha
        ]
    open_unpublished = [f for f in unpublished_findings if f.get("status", "open") == "open"]
    # In-diff findings become inline comments. Out-of-diff findings are disabled:
    # they are never surfaced on the PR (any legacy in-state ones are treated as
    # hidden).
    in_diff_unpublished = [f for f in unpublished_findings if f.get("in_diff", True)]
    eligible = filter_findings_for_publish(
        in_diff_unpublished, severity_threshold=severity_threshold, cap=cap
    )

    severity_rank = SEVERITY_ORDER[severity_threshold]
    eligible_ids = {f.get("id") for f in eligible}
    additional_findings_count = sum(
        1
        for f in in_diff_unpublished
        if f.get("id") not in eligible_ids
        and f.get("status", "open") == "open"
        and SEVERITY_ORDER.get(f.get("severity", "low"), 0) < severity_rank
    )

    inline_comments: list[dict[str, Any]] = []
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]] = []
    for finding in eligible:
        payload = render_inline_comment_payload(finding)
        if payload is None:
            continue
        inline_comments.append(payload)
        eligible_with_payload.append((finding, payload))

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
    if not inline_comments and await _open_swe_already_reviewed(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        is_re_review=is_re_review,
    ):
        resolved_thread_count = await _resolve_threads_for_resolved_findings(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
            findings=findings,
        )
        await set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)
        await clear_review_started_comment(thread_id=thread_id, owner=owner, repo=repo, token=token)
        check_finding_count = await _review_check_finding_count(thread_id, 0)
        conclusion, check_title, check_summary = review_check_conclusion(check_finding_count)
        await settle_review_check_run(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            token=token,
            conclusion=conclusion,
            title=check_title,
            summary=check_summary,
        )
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
        trace_url=review_trace_url,
        ui_url=review_ui_url,
        additional_findings_count=additional_findings_count,
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
            state=state,
        )
        if dropped_ids and valid_with_payload:
            retry_inline = [p for _, p in valid_with_payload]
            retry_body = render_review_body(
                pr_number=pr_number,
                surfaced_count=len(retry_inline),
                trace_url=review_trace_url,
                ui_url=review_ui_url,
                additional_findings_count=additional_findings_count,
            )
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
            # Either nothing to drop (no diff_line_set available, so we can't
            # tell which findings are bad) or everything would be dropped.
            # Either way, do not retry — surface the structural signal so the
            # agent stops retrying with the same args.
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
        # Defensive guard: with the upstream change this should never happen,
        # but keep a clear signal if it does so the agent doesn't retry blindly.
        return {
            "success": False,
            "error": "Failed to POST PR review: no response from GitHub",
        }
    review_id = review_response.get("id") if isinstance(review_response, dict) else None

    if review_id is not None and inline_comments:
        # Record the GitHub review id AND inline comment ids in a single
        # findings write. Previously these were three separate read-replace
        # cycles (out-of-diff review id, inline review id, comment ids); each
        # extra write widened the window where a crash could leave findings
        # half-stamped — surfaced on GitHub but with no recorded comment id, so
        # a later resolve-on-fix couldn't find the thread.
        comment_records: list[dict[str, Any]] = []
        if inline_comments:
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
        await _record_review_publication(
            thread_id=thread_id,
            review_id=review_id,
            inline_with_payload=eligible_with_payload,
            comment_records=comment_records,
            langgraph_run_id=langgraph_run_id,
        )

    if review_id is not None and inline_comments:
        current_findings = await list_findings_async(thread_id)
        if _missing_comment_ids_for_published_findings(current_findings, eligible_with_payload):
            await _backfill_findings_from_pr_threads(
                thread_id=thread_id,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                token=token,
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
    await clear_review_started_comment(thread_id=thread_id, owner=owner, repo=repo, token=token)
    check_finding_count = await _review_check_finding_count(thread_id, len(inline_comments))
    conclusion, check_title, check_summary = review_check_conclusion(check_finding_count)
    await settle_review_check_run(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        token=token,
        conclusion=conclusion,
        title=check_title,
        summary=check_summary,
    )
    if review_id is not None and eligible_with_payload:
        await _maybe_dispatch_review_autofix(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            branch_name=branch_name,
            token=token,
            surfaced_findings=[finding for finding, _payload in eligible_with_payload],
        )

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


def _review_autofix_finding_detail(finding: Finding) -> str:
    severity = str(finding.get("severity") or "medium").upper()
    file_path = str(finding.get("file") or "unknown file")
    line = finding.get("end_line")
    title = str(finding.get("title") or DEFAULT_FINDING_TITLE)
    description = str(finding.get("description") or "").strip()
    return f"[{severity}] {file_path}:{line if isinstance(line, int) else '?'} — {title}: {description}"


def _thread_matches_review_pr(
    thread: Mapping[str, Any], owner: str, repo: str, branch_name: str
) -> bool:
    """Reject threads that did not produce this PR's branch.

    Agent thread UUIDs are embedded in branch names, and branch names are
    author-controlled, so the UUID alone must never select a dispatch target:
    the resolved thread's own persisted metadata has to point back at the same
    branch (and repository, when the thread recorded one).
    """
    metadata = thread.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    if metadata.get("kind") == REVIEWER_THREAD_KIND:
        return False
    if metadata.get("branch_name") != branch_name:
        return False
    repo_config = metadata.get("repo")
    if isinstance(repo_config, Mapping) and (
        str(repo_config.get("owner", "")).lower() != owner.lower()
        or str(repo_config.get("name", "")).lower() != repo.lower()
    ):
        return False
    return True


async def _resolve_review_autofix_thread(
    client: Any, owner: str, repo: str, branch_name: str
) -> tuple[str, dict[str, Any]]:
    thread_id = get_thread_id_from_branch(branch_name)
    if thread_id:
        thread = await client.threads.get(thread_id)
        if not _thread_matches_review_pr(thread, owner, repo, branch_name):
            raise RuntimeError(
                f"thread {thread_id} does not match {owner}/{repo} branch {branch_name!r}; "
                "refusing auto-fix dispatch"
            )
        return thread_id, thread
    threads = await client.threads.search(metadata={"branch_name": branch_name}, limit=10)
    for thread in threads:
        if _thread_matches_review_pr(thread, owner, repo, branch_name):
            return thread["thread_id"], thread
    raise RuntimeError(f"could not resolve implementation thread from branch {branch_name!r}")


async def _maybe_dispatch_review_autofix(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    branch_name: str,
    token: str,
    surfaced_findings: list[Finding],
) -> None:
    enabled, threshold = await get_team_autofix_settings()
    if not enabled:
        return
    severity_rank = SEVERITY_ORDER[_cast_severity(threshold)]
    qualifying_findings = [
        finding
        for finding in surfaced_findings
        if SEVERITY_ORDER.get(finding.get("severity", "low"), 0) >= severity_rank
    ]
    if not qualifying_findings:
        return

    implementation_thread_id: str | None = None
    pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    try:
        if not await is_review_repo_enabled(owner, repo):
            return
        if await is_pr_autofix_disabled(owner, repo, pr_number):
            return
        client = dispatch_client()
        implementation_thread_id, thread = await _resolve_review_autofix_thread(
            client, owner, repo, branch_name
        )
        metadata = thread["metadata"]
        github_login = metadata.get("github_login")
        if github_login:
            profile = await load_profile(github_login)
            if isinstance(profile, dict) and profile.get("auto_fix_ci") is False:
                return
        source = metadata.get("source")
        if not source:
            raise RuntimeError(
                f"implementation thread {implementation_thread_id} has no source metadata"
            )
        configurable: dict[str, Any] = {
            "thread_id": implementation_thread_id,
            "source": source,
            "repo": {"owner": owner, "name": repo},
            "pr_number": pr_number,
            "pr_url": pr_url,
            "head_sha": head_sha,
        }
        for metadata_key, configurable_key in (
            ("github_login", "github_login"),
            ("triggering_user_email", "user_email"),
        ):
            value = metadata.get(metadata_key)
            if value is not None:
                configurable[configurable_key] = value
        source_context = metadata.get("source_context")
        if isinstance(source_context, dict):
            for key in ("slack_thread", "linear_issue", "github_issue"):
                value = source_context.get(key)
                if isinstance(value, dict):
                    configurable[key] = value

        cycle_count = await get_pr_autofix_cycle_count(owner, repo, pr_number)
        if cycle_count >= _AUTOFIX_MAX_CYCLES:
            await post_autofix_status_check(
                owner=owner,
                repo=repo,
                head_sha=head_sha,
                token=token,
                title="Auto-fix cycle limit reached",
                summary=(
                    "Open SWE did not dispatch another fix run because this pull request "
                    f"has reached the {_AUTOFIX_MAX_CYCLES}-cycle limit."
                ),
                details_url=dashboard_thread_url(implementation_thread_id),
            )
            return

        details = [
            f"PR: {pr_url}",
            f"Head SHA: {head_sha}",
            *[_review_autofix_finding_detail(finding) for finding in qualifying_findings],
        ]
        store = get_store()
        pending_namespace = ("autofix", implementation_thread_id)
        await store.aput(
            pending_namespace,
            "pending_event",
            {"reason": "Open SWE Review surfaced findings for auto-fix.", "details": details},
        )
        next_cycle = cycle_count + 1
        try:
            await dispatch_agent_run(
                implementation_thread_id,
                _AUTOFIX_NUDGE,
                configurable,
                source="review_autofix",
                client=client,
            )
        except BaseException:
            # Accepted runs may outlive cancellation; pending_event is best-effort PR context.
            await store.adelete(pending_namespace, "pending_event")
            raise
        # Deliberate ordering: count the cycle only after dispatch succeeds, so a
        # transient dispatch failure never burns the small cycle budget. The
        # opposite race (dispatch accepted, count write fails) at worst allows one
        # extra cycle, and while the store is down later publishes fail closed on
        # the cycle read anyway.
        await set_pr_autofix_cycle_count(owner, repo, pr_number, next_cycle)
        await post_autofix_status_check(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            token=token,
            title=f"Auto-fix cycle {next_cycle} dispatched",
            summary=(
                f"Open SWE dispatched auto-fix cycle {next_cycle} of {_AUTOFIX_MAX_CYCLES} "
                f"for {len(qualifying_findings)} newly surfaced review finding(s)."
            ),
            details_url=dashboard_thread_url(implementation_thread_id),
        )
    except Exception as exc:
        logger.exception("Review auto-fix producer failed for %s/%s#%s", owner, repo, pr_number)
        await post_autofix_status_check(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            token=token,
            title="Auto-fix dispatch failed",
            summary=f"Open SWE could not dispatch auto-fix for {pr_url}: {type(exc).__name__}",
            details_url=(
                dashboard_thread_url(implementation_thread_id) if implementation_thread_id else None
            ),
        )


async def _open_swe_already_reviewed(
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


def _has_publication_identity(finding: Finding) -> bool:
    return isinstance(finding.get("github_review_comment_id"), int) or isinstance(
        finding.get("github_review_id"), int
    )


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _comment_ids_for_finding(finding: Finding) -> list[int]:
    comment_ids = _int_list(finding.get("github_review_comment_ids"))
    comment_id = finding.get("github_review_comment_id")
    if isinstance(comment_id, int) and comment_id not in comment_ids:
        comment_ids.insert(0, comment_id)
    return comment_ids


def _thread_ids_for_finding(finding: Finding) -> list[str]:
    thread_ids = _str_list(finding.get("github_review_thread_ids"))
    thread_id = finding.get("github_review_thread_id")
    if isinstance(thread_id, str) and thread_id and thread_id not in thread_ids:
        thread_ids.insert(0, thread_id)
    return thread_ids


async def _backfill_findings_from_pr_threads(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> list[Finding]:
    findings = await list_findings_async(thread_id)
    if not findings:
        return findings
    review_threads = await fetch_pr_review_threads(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    if not review_threads:
        return findings
    return await reconcile_findings_with_review_threads(thread_id, review_threads)


def _missing_comment_ids_for_published_findings(
    findings: list[Finding],
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]],
) -> bool:
    finding_ids = {
        finding.get("id")
        for finding, _payload in eligible_with_payload
        if isinstance(finding.get("id"), str)
    }
    for finding in findings:
        if finding.get("id") in finding_ids and not isinstance(
            finding.get("github_review_comment_id"), int
        ):
            return True
    return False


def _apply_review_id(
    findings: list[Finding],
    *,
    finding_ids: set[str],
    review_id: int,
) -> bool:
    updated = False
    for finding in findings:
        if finding.get("id") in finding_ids and finding.get("github_review_id") != review_id:
            finding["github_review_id"] = review_id
            if isinstance(finding.get("id"), str):
                surface = _coerce_surface(finding, str(finding["id"]))
                surface["github_review_id"] = review_id
                finding["surface"] = surface
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
        finding["github_review_comment_id"] = comment_id
        comment_ids = _int_list(finding.get("github_review_comment_ids"))
        if comment_id not in comment_ids:
            comment_ids.append(comment_id)
            finding["github_review_comment_ids"] = comment_ids
        surface = _coerce_surface(finding, finding_id)
        surface["state"] = "surfaced"
        surface["github_review_comment_id"] = comment_id
        surface["severity_threshold_at_publish"] = finding.get("severity")
        surface["surfaced_at_sha"] = finding.get("last_confirmed_sha") or finding.get(
            "first_seen_sha"
        )
        finding["surface"] = surface
        if langgraph_run_id:
            finding["github_review_run_id"] = langgraph_run_id
        updated = True
    return updated


def _comment_id_by_finding_id(
    eligible_with_payload: list[tuple[Finding, dict[str, Any]]],
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
    ``_backfill_findings_from_pr_threads`` recovers them via the same marker
    against the PR's review threads.
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


async def _record_review_publication(
    *,
    thread_id: str,
    review_id: int,
    inline_with_payload: list[tuple[Finding, dict[str, Any]]],
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
        finding.get("id")
        for finding, _payload in inline_with_payload
        if isinstance(finding.get("id"), str)
    }
    comment_id_by_finding_id = _comment_id_by_finding_id(inline_with_payload, comment_records)

    latest = await list_findings_async(thread_id)
    changed = _apply_review_id(
        latest,
        finding_ids={fid for fid in review_finding_ids if isinstance(fid, str)},
        review_id=review_id,
    )
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


async def _resolve_diff_line_set(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    state: dict[str, Any] | None = None,
) -> dict[str, dict[str, set[int]]] | None:
    """Return the new-side line set for the PR diff, fetching it if needed.

    Reviewer runs clear ``configurable['diff_line_set']`` before the agent
    starts (so ``add_finding`` trusts the agent's anchors), which means the
    publish-time retry path can't rely on it being populated. Fetch the PR's
    unified diff from the GitHub REST API and recompute the line set on the
    fly. Returns ``None`` if the fetch fails — caller treats that as "we
    can't tell which finding is bad, don't retry blindly".
    """
    if isinstance(state, dict):
        state_cached = state.get("diff_line_set")
        if isinstance(state_cached, dict):
            return state_cached
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
    state: dict[str, Any] | None = None,
) -> tuple[list[tuple[Finding, dict[str, Any]]], list[str]]:
    """Drop findings whose path/line range is not in the current PR diff.

    Returns ``(valid_with_payload, dropped_finding_ids)``. When the diff
    cannot be resolved (fetch failed and no cached set), we return everything
    unchanged and an empty drop list — the caller will then surface the
    original error rather than retry blindly.
    """
    diff_line_set = await _resolve_diff_line_set(
        owner=owner, repo=repo, pr_number=pr_number, token=token, state=state
    )
    if diff_line_set is None:
        return list(eligible_with_payload), []

    valid: list[tuple[Finding, dict[str, Any]]] = []
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
    text = f"{headline} <{review_url}|View review>"

    await post_slack_thread_reply(channel_id, thread_ts, text)


async def _store_thread_ids_on_findings(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> None:
    findings = await list_findings_async(thread_id)
    comment_ids_by_finding_id: dict[str, list[int]] = {}
    for finding in findings:
        finding_id = finding.get("id")
        comment_ids = _comment_ids_for_finding(finding)
        if isinstance(finding_id, str) and comment_ids and not _thread_ids_for_finding(finding):
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
        thread_ids = _thread_ids_for_finding(finding)
        for comment_id in comment_ids_by_finding_id.get(finding_id, []):
            github_thread_id = thread_id_by_comment_id.get(comment_id)
            if not github_thread_id:
                continue
            if not isinstance(finding.get("github_review_thread_id"), str):
                finding["github_review_thread_id"] = github_thread_id
                updated = True
            if github_thread_id not in thread_ids:
                thread_ids.append(github_thread_id)
                finding["github_review_thread_ids"] = thread_ids
                updated = True
            surface = _coerce_surface(finding, finding_id)
            surface["state"] = "surfaced"
            surface["github_review_thread_id"] = github_thread_id
            finding["surface"] = surface
            updated = True

    if updated:
        await replace_findings(thread_id, findings)


async def _resolve_threads_for_resolved_findings(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    findings: list[Finding],
) -> int:
    """Resolve GitHub review threads for findings that just transitioned to resolved.

    Posts a resolution comment to the thread, then resolves it. Multiple threads
    can exist when an earlier run duplicated a comment before publication identity
    was backfilled.
    """
    resolved_count = 0
    mutated = False
    for finding in findings:
        status = finding.get("status")
        if status not in {"resolved", "dismissed"}:
            continue

        thread_node_ids = _thread_ids_for_finding(finding)
        comment_ids = _comment_ids_for_finding(finding)

        for comment_id in comment_ids:
            thread_node_id = await fetch_review_thread_id_for_comment(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                review_comment_id=comment_id,
                token=token,
            )
            if thread_node_id and thread_node_id not in thread_node_ids:
                thread_node_ids.append(thread_node_id)

        if not thread_node_ids:
            continue

        resolved_thread_ids = _str_list(finding.get("github_resolved_thread_ids"))
        posted_resolution_comment_ids = _int_list(
            finding.get("github_posted_resolution_comment_ids")
        )

        for idx, thread_node_id in enumerate(thread_node_ids):
            if thread_node_id in resolved_thread_ids:
                continue

            primary_comment_id = comment_ids[idx] if idx < len(comment_ids) else None
            resolution_body = render_resolution_comment(finding, status)
            if (
                primary_comment_id
                and primary_comment_id not in posted_resolution_comment_ids
                and resolution_body is not None
            ):
                reply_response = await reply_to_review_comment(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    review_comment_id=primary_comment_id,
                    body=resolution_body,
                    token=token,
                )
                if reply_response and isinstance(reply_response.get("id"), int):
                    posted_resolution_comment_ids.append(primary_comment_id)
                    mutated = True

            ok = await resolve_review_thread(thread_node_id=thread_node_id, token=token)
            if ok:
                resolved_thread_ids.append(thread_node_id)
                resolved_count += 1
                mutated = True

        if resolved_thread_ids:
            finding["github_resolved_thread_ids"] = resolved_thread_ids
        if posted_resolution_comment_ids:
            finding["github_posted_resolution_comment_ids"] = posted_resolution_comment_ids
        if thread_node_ids:
            finding["github_review_thread_ids"] = thread_node_ids
            if not isinstance(finding.get("github_review_thread_id"), str):
                finding["github_review_thread_id"] = thread_node_ids[0]
        if thread_node_ids and all(
            thread_id in resolved_thread_ids for thread_id in thread_node_ids
        ):
            finding["github_thread_resolved"] = True
            if isinstance(finding.get("id"), str):
                surface = _coerce_surface(finding, str(finding["id"]))
                surface["state"] = "resolved"
                if thread_node_ids:
                    surface["github_review_thread_id"] = thread_node_ids[0]
                finding["surface"] = surface

    if mutated:
        thread_id = get_thread_id_from_runtime()
        await replace_findings(thread_id, findings)

    return resolved_count


def _current_run_id(config: Mapping[str, Any]) -> str | None:
    candidates = [config.get("run_id")]
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        candidates.append(configurable.get("run_id"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
