"""Tool: ``publish_review``. Post the findings list to GitHub as a PR Review."""

from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from ..review.findings import (
    REVIEW_FINDING_CAP,
    SEVERITIES,
    ReviewerThreadMissingError,
    Severity,
    coerce_severity,
    get_thread_id_from_runtime,
    thread_missing_tool_result,
)
from ..review.publish_flow import publish_review as publish_review_flow
from ..review.publish_flow import publish_review_dry_run
from ..utils.github_token import (
    GitHubAuthError,
    get_github_token,
    invalidate_cached_github_token,
)


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
    if severity_threshold not in SEVERITIES:
        return {"success": False, "error": f"Invalid severity_threshold: {severity_threshold}"}

    config = get_config()
    raw_configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    repo_config = configurable.get("repo")
    pr_number = configurable.get("pr_number")
    head_sha = configurable.get("head_sha")

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

    if configurable.get("reviewer_eval") is True or configurable.get("eval") is True:
        threshold, cap = _eval_publication_limits(configurable, severity_threshold)
        try:
            return await publish_review_dry_run(
                head_sha=head_sha, severity_threshold=threshold, cap=cap
            )
        except ReviewerThreadMissingError as exc:
            return thread_missing_tool_result(exc)

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    try:
        return await publish_review_flow(
            owner=str(repo_config["owner"]),
            repo=str(repo_config["name"]),
            pr_number=pr_number,
            head_sha=head_sha,
            token=token,
            severity_threshold=coerce_severity(severity_threshold),
            cap=REVIEW_FINDING_CAP,
            is_re_review=bool(configurable.get("re_review")),
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


def _eval_publication_limits(
    configurable: dict[str, Any], default_threshold: str
) -> tuple[Severity, int]:
    """The severity floor and cap a benchmark run publishes against."""
    threshold = configurable.get("reviewer_eval_severity_threshold")
    cap = configurable.get("reviewer_eval_cap")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
        cap = REVIEW_FINDING_CAP
    return coerce_severity(threshold if threshold in SEVERITIES else default_threshold), cap


def _current_run_id(config: Mapping[str, Any]) -> str | None:
    configurable = config.get("configurable")
    candidates = (
        config.get("run_id"),
        configurable.get("run_id") if isinstance(configurable, dict) else None,
    )
    return next((c for c in candidates if isinstance(c, str) and c), None)
