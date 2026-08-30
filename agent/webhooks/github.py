"""GitHub webhook handlers — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

import asyncio
import re
import uuid
import weakref
from datetime import UTC, datetime
from typing import Any, cast, get_args

from ..dashboard.autofix_state import set_pr_autofix_disabled
from ..review.findings import FindingInteraction, ReviewerPRMeta, ReviewerSlackThread
from ..review.publish import settle_review_check_run
from ..utils.github_checks import CheckConclusion
from ..utils.github_comments import GitHubAuthError
from ..utils.github_org_membership import is_internal_bot_login
from ..utils.slack import GitHubPrRef
from . import common

_REVIEW_START_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_REVIEW_START_CLAIM_TTL_SECONDS = 60


def _review_start_now() -> float:
    return datetime.now(UTC).timestamp()


def _review_start_lock(thread_id: str) -> asyncio.Lock:
    lock = _REVIEW_START_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _REVIEW_START_LOCKS[thread_id] = lock
    return lock


def _review_run_metadata(check_run_id: int | None) -> dict[str, Any]:
    """Attach the full-review check id to immutable run metadata."""
    metadata: dict[str, Any] = dict(common._AGENT_VERSION_METADATA)
    if check_run_id is not None:
        metadata["review_check_run_id"] = check_run_id
    return metadata


def _full_review_in_flight_for_head(metadata: dict[str, Any] | None, head_sha: str) -> bool:
    """Return whether a full review of ``head_sha`` has been dispatched and not published.

    The synchronize/opened dispatch records ``head_sha`` on the reviewer thread
    before starting the review, and ``publish_review`` advances
    ``last_reviewed_sha`` to the head only when that review publishes. Between
    those two writes the head is under review. This is judged from the thread's
    own state, not from the review check-run: the check is best-effort (it is
    ``None`` when the App lacks Checks permission or the API fails) and must
    not decide whether a review is preemptable.

    A finding reply must not interrupt that review: the reply run only
    reassesses one thread and cannot honestly conclude a check for a head
    nobody reviewed, so the handed-over check would settle as incomplete and
    the head would stay unreviewed until the next push (MASTRA-159). Instead
    the reply is dispatched with ``multitask_strategy="enqueue"`` so it runs
    after the review — late replies are answered, never dropped, and if the
    review already ended the enqueued run simply starts at once.
    """
    if not isinstance(metadata, dict) or not head_sha:
        return False
    return metadata.get("head_sha") == head_sha and metadata.get("last_reviewed_sha") != head_sha


async def _settle_review_check_before_finding_reply(
    *,
    thread_id: str,
    metadata: dict[str, Any] | None,
    owner: str,
    repo: str,
    token: str,
) -> int | None:
    """Resolve the full-review check before a finding reply interrupts its owner.

    Returns the check handed to the successor, so the dispatch can record it on
    the run itself — the after-agent hook only fires when the graph exits
    normally, and a run killed by a timeout or a hard error needs the
    completion handler to find the check and close it.

    A verdict the owner already staged (``pending``) is published, since that
    review did reach a conclusion. Otherwise the check is deliberately *not*
    concluded: it is handed to the run we are about to dispatch, which reports
    the real result once it publishes.

    Concluding here instead — with any non-failure result — would leave the
    head momentarily unguarded, and permanently so if the successor never
    publishes, letting a blocking check pass without a completed review.
    Leaving it in progress keeps the gate closed for the whole handoff, and
    the after-agent hook still settles it if the successor dies.
    """
    if not isinstance(metadata, dict):
        return None
    check_run_id = metadata.get("review_check_run_id")
    if not isinstance(check_run_id, int):
        return None
    deferred = metadata.get("review_check_deferred_result")
    if isinstance(deferred, dict) and deferred.get("review_check_run_id") == check_run_id:
        return None
    pending = metadata.get("review_check_pending_result")
    if (
        isinstance(pending, dict)
        and pending.get("review_check_run_id") == check_run_id
        and pending.get("conclusion") in get_args(CheckConclusion)
    ):
        try:
            await settle_review_check_run(
                thread_id=thread_id,
                owner=owner,
                repo=repo,
                token=token,
                conclusion=cast(CheckConclusion, pending["conclusion"]),
                title=str(pending.get("title") or "Review completed"),
                summary=str(pending.get("summary") or ""),
                expected_check_run_id=check_run_id,
            )
        except Exception:
            common.logger.warning(
                "Could not settle review check before finding reply dispatch for %s",
                thread_id,
                exc_info=True,
            )
        return None
    await common.set_reviewer_thread_metadata(
        thread_id,
        extra={"review_check_superseded": {"review_check_run_id": check_run_id}},
    )
    return check_run_id


def build_github_issue_prompt(
    repo_config: dict[str, str],
    issue_number: int,
    issue_id: str,
    title: str,
    body: str,
    comments: list[dict[str, Any]],
    *,
    github_login: str,
    issue_author: str = "",
    issue_url: str = "",
) -> str:
    """Build the user prompt for a GitHub issue-triggered run."""
    triggered_by_line = f"## Triggered by: {github_login}\n\n" if github_login else ""
    issue_url_line = f"## Issue URL: {issue_url}\n\n" if issue_url else ""
    comments_text = common._build_github_issue_comments_text(comments)
    sanitized_title = common.sanitize_github_comment_body(title)
    formatted_body = common.format_github_comment_body_for_prompt(
        issue_author or github_login, body
    )
    return (
        "Please work on the following GitHub issue:\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"{triggered_by_line}"
        f"## GitHub Issue: #{issue_number} - Issue ID: {issue_id}\n\n"
        f"{issue_url_line}"
        f"## Title: {sanitized_title}\n\n"
        f"## Description:\n{formatted_body}\n"
        f"{comments_text}\n\n"
        "Please analyze this issue and implement the necessary changes. "
        "If you open a PR for this issue, make sure the PR description links back to "
        "this issue and follows this repository's PR conventions for the title, body, "
        "release note, and/or changelog. Inspect AGENTS.md, PR templates, "
        ".changelog/README.md, and nearby docs before choosing the PR title/body format. "
        "When you need to communicate on GitHub, use `GH_TOKEN=dummy gh issue comment` "
        "with the issue number."
    )


def build_github_issue_followup_prompt(github_login: str, comment_body: str) -> str:
    """Build the prompt for a follow-up GitHub issue comment."""
    return f"**{github_login}:**\n{common.format_github_comment_body_for_prompt(github_login, comment_body)}"


def build_github_issue_update_prompt(github_login: str, title: str, body: str) -> str:
    """Build the prompt for a follow-up GitHub issue title/body update."""
    sanitized_title = common.sanitize_github_comment_body(title)
    formatted_body = common.format_github_comment_body_for_prompt(github_login, body)
    return (
        f"**{github_login}:** updated the GitHub issue title/body.\n\n"
        f"Title: {sanitized_title}\n\n"
        f"Description:\n{formatted_body}"
    )


def build_github_pr_review_prompt(
    repo_config: dict[str, str],
    pr_number: int,
    pr_url: str,
    base_sha: str,
    head_sha: str,
) -> str:
    """Build the user prompt for a reviewer-agent run."""
    return (
        "Please review this GitHub pull request.\n\n"
        f"## Repository: {repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"## Pull Request: {pr_url}\n\n"
        f"## PR Number: {pr_number}\n\n"
        f"## Base SHA: {base_sha}\n\n"
        f"## Head SHA: {head_sha}\n\n"
        "Submit findings as inline GitHub review comments. If there are no real issues, "
        "submit no comments."
    )


async def trigger_pr_review_from_ref(
    pr_ref: GitHubPrRef,
    *,
    source: str,
    github_login: str = "",
    github_user_id: int | None = None,
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
) -> dict[str, Any]:
    repo_config = {"owner": pr_ref.owner, "name": pr_ref.repo}

    app_token, app_token_expires_at = await common.get_github_app_installation_token_with_expiry(
        target_repo=f"{pr_ref.owner}/{pr_ref.repo}", repositories=[pr_ref.repo]
    )
    if not app_token:
        common.logger.warning("No GitHub App token available for PR reviewer request")
        return {"success": False, "error": "No GitHub App token available"}

    pr_metadata = await common.fetch_github_pr_metadata(pr_ref, token=app_token)
    if not pr_metadata:
        return {"success": False, "error": "Could not fetch pull request metadata"}

    repo_private = common._repo_private_from_pr_metadata(pr_metadata)

    base_sha = pr_metadata.get("base", {}).get("sha", "")
    head = pr_metadata.get("head", {})
    head_sha = head.get("sha", "")
    branch_name = head.get("ref", "")
    base_ref = pr_metadata.get("base", {}).get("ref", "")
    pr_title = pr_metadata.get("title", "")
    pr_url = pr_metadata.get("html_url", "") or pr_ref.url
    if not base_sha or not head_sha:
        common.logger.warning("Missing base/head SHA for Slack PR review request")
        return {"success": False, "error": "Pull request metadata is missing base/head SHA"}

    thread_id = common.generate_reviewer_thread_id(pr_ref.owner, pr_ref.repo, pr_ref.number)
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common._ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return {"success": False, "error": "Could not create reviewer thread"}
    reviewer_metadata = await common._get_thread_metadata_safe(thread_id)
    explicit_request_requires_stock = reviewer_metadata is None
    existing_check_run_id = (
        reviewer_metadata.get("review_check_run_id")
        if isinstance(reviewer_metadata, dict)
        else None
    )
    if (
        reviewer_metadata is not None
        and reviewer_metadata.get("kind") == common.REVIEWER_THREAD_KIND
    ):
        last_reviewed_sha = reviewer_metadata.get("last_reviewed_sha")
        explicit_request_requires_stock = bool(
            isinstance(last_reviewed_sha, str) and last_reviewed_sha
        )

    pr_meta: ReviewerPRMeta = {
        "owner": pr_ref.owner,
        "name": pr_ref.repo,
        "number": pr_ref.number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": branch_name,
        "base_ref": base_ref,
        "author": (pr_metadata.get("user") or {}).get("login", ""),
    }
    slack_thread_meta: ReviewerSlackThread | None = None
    if slack_channel_id and slack_thread_ts:
        slack_thread_meta = {
            "channel_id": slack_channel_id,
            "thread_ts": slack_thread_ts,
        }
    await common.set_reviewer_thread_metadata(
        thread_id, pr=pr_meta, watch=True, slack_thread=slack_thread_meta, head_sha=head_sha
    )
    await common.post_review_started_comment(
        thread_id=thread_id,
        owner=pr_ref.owner,
        repo=pr_ref.repo,
        pr_number=pr_ref.number,
        token=app_token,
    )

    prompt = build_github_pr_review_prompt(repo_config, pr_ref.number, pr_url, base_sha, head_sha)
    configurable = common._build_reviewer_configurable(
        source=source,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_ref.number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=branch_name,
        repo_private=repo_private,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
    )
    if isinstance(existing_check_run_id, int):
        configurable["review_check_run_id"] = existing_check_run_id
    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=explicit_request_requires_stock,
        finding_reply=False,
        explicit_request=True,
    )

    common.logger.info(
        "Dispatching reviewer run for thread %s from %s PR review request", thread_id, source
    )
    run = await common.dispatch_agent_run(
        thread_id,
        prompt,
        configurable,
        source=source,
        assistant_id=assistant_id,
        metadata=_review_run_metadata(
            existing_check_run_id if isinstance(existing_check_run_id, int) else None
        ),
        client=langgraph_client,
    )
    await common._store_current_reviewer_run_id(thread_id, run)
    return {"success": True, "queued": False, "thread_id": thread_id, "pr_url": pr_url}


async def _dispatch_first_review_from_pr_payload(payload: dict[str, Any], *, source: str) -> None:
    """Trigger a first-review run on the canonical reviewer thread for a PR."""
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    repo_private = common._repo_private_from_payload(payload)
    repo_id = common._repo_id_from_payload(payload)
    pr_number = pull_request.get("number")
    pr_url = pull_request.get("html_url", "") or pull_request.get("url", "")
    branch_name = pull_request.get("head", {}).get("ref", "")
    base_ref = pull_request.get("base", {}).get("ref", "")
    base_sha = pull_request.get("base", {}).get("sha", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    pr_title = pull_request.get("title", "")
    github_login = payload.get("sender", {}).get("login", "")
    github_user_id = payload.get("sender", {}).get("id")

    if not pr_number or not pr_url or not base_sha or not head_sha:
        common.logger.warning("Missing PR context for reviewer dispatch, skipping run")
        return

    thread_id = common.generate_reviewer_thread_id(
        repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
    )

    pr_meta: ReviewerPRMeta = {
        "owner": repo_config.get("owner", ""),
        "name": repo_config.get("name", ""),
        "number": pr_number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": branch_name,
        "base_ref": base_ref,
        "author": (pull_request.get("user") or {}).get("login", ""),
    }
    last_reviewed_sha = ""
    metadata: dict[str, Any] | None = None
    if payload.get("action") == "ready_for_review":
        metadata = await common._get_thread_metadata_safe(thread_id)
        if metadata is not None and metadata.get("kind") == common.REVIEWER_THREAD_KIND:
            existing_last_reviewed_sha = metadata.get("last_reviewed_sha")
            if isinstance(existing_last_reviewed_sha, str) and existing_last_reviewed_sha:
                last_reviewed_sha = existing_last_reviewed_sha
    initial_check_run_id = (
        metadata.get("review_check_run_id") if isinstance(metadata, dict) else None
    )
    if not isinstance(initial_check_run_id, int):
        initial_check_run_id = None

    app_token, app_token_expires_at = await common._reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        common.logger.warning("No GitHub App token available for reviewer dispatch")
        return

    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common._ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return

    await common.set_reviewer_thread_metadata(thread_id, pr=pr_meta, watch=True, head_sha=head_sha)

    current_pr = await common.fetch_github_pr_metadata(
        GitHubPrRef(
            owner=repo_config["owner"],
            repo=repo_config["name"],
            number=pr_number,
            url=pr_url,
        ),
        token=app_token,
    )
    if current_pr is None:
        common.logger.warning(
            "First review for %s/%s#%s head=%s could not refresh PR metadata; "
            "using webhook payload",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
        )
    else:
        pull_request = current_pr
        if pull_request.get("state") == "closed":
            await common.set_reviewer_thread_metadata(thread_id, watch=False)
            common.logger.info(
                "Skipping first review for %s/%s#%s: PR closed during startup",
                repo_config["owner"],
                repo_config["name"],
                pr_number,
            )
            return
        if pull_request.get("draft") and not await common._draft_review_enabled_for_author(
            (pull_request.get("user") or {}).get("login", "")
        ):
            await common.set_reviewer_thread_metadata(thread_id, watch=False)
            common.logger.info(
                "Skipping first review for %s/%s#%s: PR became an ineligible draft during startup",
                repo_config["owner"],
                repo_config["name"],
                pr_number,
            )
            return
        pr_url = pull_request.get("html_url", "") or pull_request.get("url", "") or pr_url
        branch_name = pull_request.get("head", {}).get("ref", "")
        base_ref = pull_request.get("base", {}).get("ref", "")
        base_sha = pull_request.get("base", {}).get("sha", "")
        head_sha = pull_request.get("head", {}).get("sha", "")
        pr_title = pull_request.get("title", "")
        if not base_sha or not head_sha:
            common.logger.warning(
                "First review for %s/%s#%s head=%s not dispatched: refreshed PR metadata "
                "is missing base/head SHA",
                repo_config["owner"],
                repo_config["name"],
                pr_number,
                head_sha or "<missing>",
            )
            return
        pr_meta = {
            "owner": repo_config["owner"],
            "name": repo_config["name"],
            "number": pr_number,
            "url": pr_url,
            "title": pr_title,
            "head_ref": branch_name,
            "base_ref": base_ref,
            "author": (pull_request.get("user") or {}).get("login", ""),
        }
        await common.set_reviewer_thread_metadata(
            thread_id, pr=pr_meta, watch=True, head_sha=head_sha
        )

    if payload.get("action") == "ready_for_review" and last_reviewed_sha == head_sha:
        common.logger.info(
            "Skipping ready_for_review auto-review for %s/%s#%s: "
            "head_sha unchanged from last_reviewed_sha",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
        )
        return

    try:
        dispatch_metadata = await common._get_thread_metadata_safe(thread_id, raise_on_error=True)
    except Exception:
        common.logger.warning(
            "First review for %s/%s#%s head=%s not dispatched: could not confirm "
            "reviewer thread metadata",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
            exc_info=True,
        )
        return
    if (
        dispatch_metadata is None
        or dispatch_metadata.get("kind") != common.REVIEWER_THREAD_KIND
        or dispatch_metadata.get("watch") is not True
    ):
        common.logger.info(
            "First review for %s/%s#%s head=%s stood down: reviewer watch changed",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
        )
        return
    dispatch_head_sha = dispatch_metadata.get("head_sha")
    dispatch_check_run_id = dispatch_metadata.get("review_check_run_id")
    if dispatch_head_sha != head_sha or (dispatch_check_run_id != initial_check_run_id):
        common.logger.info(
            "First review for %s/%s#%s head=%s stood down: superseded by head=%s",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
            dispatch_head_sha,
        )
        return

    check_run_id = await common.create_review_check_run(
        owner=repo_config.get("owner", ""),
        repo=repo_config.get("name", ""),
        head_sha=head_sha,
        token=app_token,
        details_url=common.dashboard_thread_url(thread_id),
    )
    if check_run_id is not None:
        await common.set_reviewer_thread_metadata(
            thread_id, extra={"review_check_run_id": check_run_id}
        )

    is_re_review = bool(last_reviewed_sha)
    if is_re_review:
        prompt = (
            f"PR #{pr_number} has been marked ready for review. The new HEAD is "
            f"{head_sha}. Reconcile existing findings against the new diff, add any "
            f"net-new findings, and call `publish_review` once you're done."
        )
    else:
        prompt = build_github_pr_review_prompt(repo_config, pr_number, pr_url, base_sha, head_sha)
    configurable = common._build_reviewer_configurable(
        source=source,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=branch_name,
        repo_private=repo_private,
        re_review=is_re_review,
        last_reviewed_sha=last_reviewed_sha,
    )
    if check_run_id is not None:
        configurable["review_check_run_id"] = check_run_id
    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=is_re_review,
        finding_reply=False,
        explicit_request=False,
    )

    common.logger.info("Dispatching reviewer run for thread %s (source=%s)", thread_id, source)
    run = await common.dispatch_agent_run(
        thread_id,
        prompt,
        configurable,
        source=source,
        assistant_id=assistant_id,
        metadata=_review_run_metadata(check_run_id),
        client=langgraph_client,
    )
    await common._store_current_reviewer_run_id(thread_id, run)
    common.logger.info("Reviewer run dispatched for thread %s (source=%s)", thread_id, source)


async def process_github_pr_ready(payload: dict[str, Any]) -> None:
    """Auto-review a PR that has just been opened or marked ready-for-review.

    Drafts are gated by the PR author's ``review_draft_prs`` profile flag
    (with the team-wide setting as a fallback).
    """
    pull_request = payload.get("pull_request", {})
    is_draft = bool(pull_request.get("draft"))
    if is_draft:
        author = pull_request.get("user") or {}
        author_login = author.get("login", "") if isinstance(author, dict) else ""
        if not await common._draft_review_enabled_for_author(author_login):
            common.logger.info(
                "Skipping auto-review of draft PR by %s: review_draft_prs is disabled",
                author_login or "<unknown>",
            )
            return
    # Use source="github" so the reviewer resolver can use the GitHub App token;
    # "github_auto" would fall through to the email-based path, which has no
    # user_email to route on for webhook-triggered runs.
    await _dispatch_first_review_from_pr_payload(payload, source="github")


def _existing_synchronize_review_start(
    metadata: dict[str, Any], *, head_sha: str, thread_id: str
) -> dict[str, Any] | None:
    record = metadata.get("review_start")
    if isinstance(record, dict) and record.get("head_sha") == head_sha:
        status = record.get("status")
        if status == "claimed":
            claimed_at = record.get("claimed_at")
            if (
                isinstance(claimed_at, int | float)
                and _review_start_now() - claimed_at < _REVIEW_START_CLAIM_TTL_SECONDS
            ):
                return {
                    "status": "error",
                    "code": "review_start_in_progress",
                    "message": "The synchronized head has a review start claim without an accepted run.",
                    "head_sha": head_sha,
                    "thread_id": thread_id,
                    "retryable": True,
                }
            return None
        if status == "started":
            check_run_id = record.get("check_run_id")
            reviewer_run_id = record.get("reviewer_run_id")
            if isinstance(check_run_id, int) and isinstance(reviewer_run_id, str):
                return {
                    "status": "accepted",
                    "message": "Open SWE review already owns this head",
                    "ownership": "existing",
                    "head_sha": head_sha,
                    "thread_id": thread_id,
                    "check_run_id": check_run_id,
                    "reviewer_run_id": reviewer_run_id,
                }
        if status == "error":
            response = {
                "status": "error",
                "code": str(record.get("code") or "review_start_failed"),
                "message": str(record.get("message") or "Open SWE review start failed."),
                "head_sha": head_sha,
                "thread_id": thread_id,
            }
            for key in ("check_run_id", "check_settled", "reviewer_run_id", "run_cancelled"):
                if key in record:
                    response[key] = record[key]
            return response

    check_run_id = metadata.get("review_check_run_id")
    reviewer_run_id = metadata.get("current_reviewer_run_id")
    if (
        metadata.get("head_sha") == head_sha
        and isinstance(check_run_id, int)
        and isinstance(reviewer_run_id, str)
        and reviewer_run_id
    ):
        return {
            "status": "accepted",
            "message": "Open SWE review already owns this head",
            "ownership": "existing",
            "head_sha": head_sha,
            "thread_id": thread_id,
            "check_run_id": check_run_id,
            "reviewer_run_id": reviewer_run_id,
        }
    return None


async def _record_synchronize_review_error(
    *,
    thread_id: str,
    head_sha: str,
    code: str,
    message: str,
    check_run_id: int | None = None,
    check_settled: bool | None = None,
    reviewer_run_id: str | None = None,
    run_cancelled: bool | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "head_sha": head_sha,
        "status": "error",
        "code": code,
        "message": message,
    }
    if check_run_id is not None:
        record["check_run_id"] = check_run_id
    if check_settled is not None:
        record["check_settled"] = check_settled
    if reviewer_run_id is not None:
        record["reviewer_run_id"] = reviewer_run_id
    if run_cancelled is not None:
        record["run_cancelled"] = run_cancelled
    error_persisted = True
    try:
        await common.set_reviewer_thread_metadata(thread_id, extra={"review_start": record})
    except Exception:
        error_persisted = False
        common.logger.exception(
            "Could not persist synchronize review error for thread %s head=%s",
            thread_id,
            head_sha,
        )
    common.logger.error(
        "Synchronize review start failed for thread %s head=%s code=%s", thread_id, head_sha, code
    )
    response: dict[str, Any] = {
        "status": "error",
        "code": code,
        "message": message,
        "head_sha": head_sha,
        "thread_id": thread_id,
    }
    if check_run_id is not None:
        response["check_run_id"] = check_run_id
    if check_settled is not None:
        response["check_settled"] = check_settled
    if reviewer_run_id is not None:
        response["reviewer_run_id"] = reviewer_run_id
    if run_cancelled is not None:
        response["run_cancelled"] = run_cancelled
    if not error_persisted:
        response["error_persisted"] = False
    return response


async def process_github_pr_synchronize(payload: dict[str, Any]) -> dict[str, Any]:
    """Start or return review ownership for a synchronized PR head."""
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    owner = repo.get("owner", {}).get("login", "")
    name = repo.get("name", "")
    pr_number = pull_request.get("number")
    pr_url = pull_request.get("html_url", "") or pull_request.get("url", "")
    base_sha = pull_request.get("base", {}).get("sha", "")
    base_ref = pull_request.get("base", {}).get("ref", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    head_ref = pull_request.get("head", {}).get("ref", "")
    if not owner or not name or not isinstance(pr_number, int) or not base_sha or not head_sha:
        return {
            "status": "error",
            "code": "invalid_pull_request_context",
            "message": "The synchronize payload is missing repository, pull request, or head data.",
            "head_sha": head_sha,
        }

    thread_id = common.generate_reviewer_thread_id(owner, name, pr_number)
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common._ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return {
            "status": "error",
            "code": "reviewer_thread_unavailable",
            "message": "The Open SWE reviewer thread could not be prepared.",
            "head_sha": head_sha,
            "thread_id": thread_id,
        }
    try:
        metadata = await common._get_thread_metadata_safe(thread_id, raise_on_error=True)
    except Exception:
        common.logger.exception(
            "Synchronize review start could not read thread %s for head=%s", thread_id, head_sha
        )
        return {
            "status": "error",
            "code": "reviewer_metadata_unavailable",
            "message": "The Open SWE reviewer state could not be read.",
            "head_sha": head_sha,
            "thread_id": thread_id,
        }
    if (
        metadata is None
        or metadata.get("kind") != common.REVIEWER_THREAD_KIND
        or metadata.get("watch") is not True
    ):
        return {
            "status": "ignored",
            "reason": "Pull request is not watched by Open SWE Review",
            "head_sha": head_sha,
            "thread_id": thread_id,
        }

    existing = _existing_synchronize_review_start(metadata, head_sha=head_sha, thread_id=thread_id)
    if existing is not None:
        return existing

    repo_config = {"owner": owner, "name": name}
    repo_private = common._repo_private_from_payload(payload)
    repo_id = common._repo_id_from_payload(payload)
    app_token, _ = await common._reviewer_token_for_repo(
        repo_config, repo_private=repo_private, repo_id=repo_id
    )
    if not app_token:
        return await _record_synchronize_review_error(
            thread_id=thread_id,
            head_sha=head_sha,
            code="github_app_token_unavailable",
            message="A GitHub App token could not be resolved for the synchronized head.",
        )

    async with _review_start_lock(thread_id):
        try:
            metadata = await common._get_thread_metadata_safe(thread_id, raise_on_error=True)
        except Exception:
            common.logger.exception(
                "Synchronize review claim could not read thread %s for head=%s",
                thread_id,
                head_sha,
            )
            return {
                "status": "error",
                "code": "reviewer_metadata_unavailable",
                "message": "The Open SWE reviewer state could not be read.",
                "head_sha": head_sha,
                "thread_id": thread_id,
            }
        if (
            metadata is None
            or metadata.get("kind") != common.REVIEWER_THREAD_KIND
            or metadata.get("watch") is not True
        ):
            return {
                "status": "ignored",
                "reason": "Pull request is not watched by Open SWE Review",
                "head_sha": head_sha,
                "thread_id": thread_id,
            }
        existing = _existing_synchronize_review_start(
            metadata, head_sha=head_sha, thread_id=thread_id
        )
        if existing is not None:
            return existing

        pr_meta: ReviewerPRMeta = {
            "owner": owner,
            "name": name,
            "number": pr_number,
            "url": pr_url,
            "title": pull_request.get("title", ""),
            "head_ref": head_ref,
            "base_ref": base_ref,
            "author": (pull_request.get("user") or {}).get("login", ""),
        }
        claim = {
            "head_sha": head_sha,
            "status": "claimed",
            "claimed_at": _review_start_now(),
        }
        try:
            await common.set_reviewer_thread_metadata(
                thread_id, pr=pr_meta, watch=True, extra={"review_start": claim}
            )
        except Exception:
            common.logger.exception(
                "Synchronize review claim failed for thread %s head=%s", thread_id, head_sha
            )
            return {
                "status": "error",
                "code": "review_start_claim_failed",
                "message": "The synchronized head could not claim Open SWE review ownership.",
                "head_sha": head_sha,
                "thread_id": thread_id,
                "retryable": True,
            }

        try:
            threads = await common.fetch_pr_review_threads(
                owner=owner, repo=name, pr_number=pr_number, token=app_token
            )
            await common.reconcile_findings_with_review_threads(thread_id, threads)
        except Exception:
            common.logger.warning(
                "Could not sync review threads before synchronize review for %s",
                thread_id,
                exc_info=True,
            )

        check_run_id = await common.create_review_check_run(
            owner=owner,
            repo=name,
            head_sha=head_sha,
            token=app_token,
            details_url=common.dashboard_thread_url(thread_id),
        )
        if check_run_id is None:
            return await _record_synchronize_review_error(
                thread_id=thread_id,
                head_sha=head_sha,
                code="review_check_create_failed",
                message="The Open SWE Review check could not be created on the synchronized head.",
            )

        last_reviewed_sha = metadata.get("last_reviewed_sha")
        last_reviewed_sha = last_reviewed_sha if isinstance(last_reviewed_sha, str) else ""
        prompt = (
            f"A new commit has been pushed to PR #{pr_number}. The new HEAD is "
            f"{head_sha}. Reconcile existing findings against the new diff, add any "
            f"net-new findings, and call `publish_review` once you're done."
        )
        configurable = common._build_reviewer_configurable(
            source="github_synchronize",
            github_login=payload.get("sender", {}).get("login", "") or "",
            github_user_id=payload.get("sender", {}).get("id"),
            repo_config=repo_config,
            pr_number=pr_number,
            pr_url=pr_url,
            base_sha=base_sha,
            head_sha=head_sha,
            branch_name=head_ref,
            repo_private=repo_private,
            re_review=True,
            last_reviewed_sha=last_reviewed_sha,
        )
        configurable["review_check_run_id"] = check_run_id
        try:
            assistant_id = await common.reviewer_assistant_for_dispatch(
                re_review=True,
                finding_reply=False,
                explicit_request=False,
            )
            run = await common.dispatch_agent_run(
                thread_id,
                prompt,
                configurable,
                source="github_synchronize",
                assistant_id=assistant_id,
                metadata=_review_run_metadata(check_run_id),
                client=langgraph_client,
            )
            reviewer_run_id = run.get("run_id") if isinstance(run, dict) else None
            if not isinstance(reviewer_run_id, str) or not reviewer_run_id:
                raise RuntimeError("Reviewer dispatch returned no run id")
        except Exception:
            common.logger.exception(
                "Synchronize reviewer dispatch failed for thread %s head=%s", thread_id, head_sha
            )
            try:
                check_settled = await settle_review_check_run(
                    thread_id=thread_id,
                    owner=owner,
                    repo=name,
                    token=app_token,
                    conclusion="failure",
                    title="Review dispatch failed",
                    summary=(
                        "Open SWE created the review check but could not start the reviewer run. "
                        "The synchronize delivery ended with a terminal dispatch error."
                    ),
                    expected_check_run_id=check_run_id,
                )
            except Exception:
                common.logger.exception(
                    "Could not settle failed synchronize check %s for thread %s",
                    check_run_id,
                    thread_id,
                )
                check_settled = False
            return await _record_synchronize_review_error(
                thread_id=thread_id,
                head_sha=head_sha,
                code="review_dispatch_failed",
                message="The Open SWE reviewer run could not be started.",
                check_run_id=check_run_id,
                check_settled=check_settled,
            )

        review_start = {
            "head_sha": head_sha,
            "status": "started",
            "check_run_id": check_run_id,
            "reviewer_run_id": reviewer_run_id,
        }
        try:
            await common.set_reviewer_thread_metadata(
                thread_id,
                head_sha=head_sha,
                extra={
                    "review_check_run_id": check_run_id,
                    "current_reviewer_run_id": reviewer_run_id,
                    "review_start": review_start,
                },
            )
        except Exception:
            common.logger.exception(
                "Synchronize review ownership could not be finalized for thread %s head=%s",
                thread_id,
                head_sha,
            )
            try:
                await langgraph_client.runs.cancel(thread_id, reviewer_run_id, wait=True)
                run_cancelled = True
            except Exception:
                run_cancelled = False
                common.logger.exception(
                    "Could not cancel unowned synchronize reviewer run %s", reviewer_run_id
                )
            try:
                check_settled = await settle_review_check_run(
                    thread_id=thread_id,
                    owner=owner,
                    repo=name,
                    token=app_token,
                    conclusion="failure",
                    title="Review ownership failed",
                    summary=(
                        "Open SWE started the reviewer but could not persist exact-head ownership. "
                        "The run was cancelled and the synchronize delivery ended with an error."
                    ),
                    expected_check_run_id=check_run_id,
                )
            except Exception:
                check_settled = False
                common.logger.exception(
                    "Could not settle unowned synchronize check %s", check_run_id
                )
            return await _record_synchronize_review_error(
                thread_id=thread_id,
                head_sha=head_sha,
                code="review_ownership_persist_failed",
                message="The Open SWE reviewer ownership could not be persisted.",
                check_run_id=check_run_id,
                check_settled=check_settled,
                reviewer_run_id=reviewer_run_id,
                run_cancelled=run_cancelled,
            )
        return {
            "status": "accepted",
            "message": "Open SWE review started",
            "ownership": "created",
            "head_sha": head_sha,
            "thread_id": thread_id,
            "check_run_id": check_run_id,
            "reviewer_run_id": reviewer_run_id,
        }


async def process_github_pr_close(payload: dict[str, Any]) -> None:
    """Toggle watch on the canonical reviewer thread on close/reopen/draft transitions.

    ``reopened`` re-enables watch; ``closed`` always disables it.
    ``converted_to_draft`` disables watch only when the PR author's effective
    draft-review setting is off — if drafts should be reviewed, watch stays on
    so subsequent pushes still trigger re-reviews while the PR is in draft.
    """
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    pr_number = pull_request.get("number")
    if not pr_number or not isinstance(pr_number, int):
        return

    thread_id = common.generate_reviewer_thread_id(
        repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
    )
    metadata = await common._get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        # No reviewer thread for this PR, nothing to do.
        common.logger.debug(
            "PR %s/%s#%s closed/reopened: no reviewer thread, skipping watch update",
            repo_config.get("owner"),
            repo_config.get("name"),
            pr_number,
        )
        return
    action = payload.get("action", "")
    if action == "converted_to_draft":
        author = pull_request.get("user") or {}
        author_login = author.get("login", "") if isinstance(author, dict) else ""
        if await common._draft_review_enabled_for_author(author_login):
            common.logger.info(
                "PR %s/%s#%s converted to draft but author %s has draft reviews enabled; keeping watch",
                repo_config.get("owner"),
                repo_config.get("name"),
                pr_number,
                author_login or "<unknown>",
            )
            return
        desired_watch = False
    else:
        desired_watch = action == "reopened"
    if metadata.get("watch") == desired_watch:
        return
    await common.set_reviewer_thread_metadata(thread_id, watch=desired_watch)
    common.logger.info(
        "Set watch=%s on reviewer thread %s after PR %s", desired_watch, thread_id, action
    )


async def process_github_push_event(payload: dict[str, Any]) -> None:
    """Re-trigger the reviewer for a watched PR when its head branch is pushed to."""
    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", "") or repo.get("owner", {}).get("name", ""),
        "name": repo.get("name", ""),
    }
    repo_full_name = f"{repo_config['owner']}/{repo_config['name']}"
    ref = payload.get("ref", "")
    after_sha = payload.get("after", "")
    if not ref.startswith("refs/heads/"):
        common.logger.info(
            "Push ignored: repo=%s ref=%s reason=ref is not a branch", repo_full_name, ref
        )
        return
    if not isinstance(after_sha, str) or not after_sha or set(after_sha) == {"0"}:
        common.logger.info(
            "Push ignored: repo=%s ref=%s reason=branch deletion or missing SHA",
            repo_full_name,
            ref,
        )
        return
    head_ref = ref[len("refs/heads/") :]

    repo_private = common._repo_private_from_payload(payload)
    repo_id = common._repo_id_from_payload(payload)
    if not repo_config["owner"] or not repo_config["name"]:
        common.logger.warning(
            "Push to %s ignored: repository owner/name missing from payload", head_ref
        )
        return
    if not await common._is_repo_auto_review_enabled(repo_config):
        common.logger.info(
            "Push to %s/%s head=%s ignored: automatic review disabled",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    app_token, app_token_expires_at = await common._reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        common.logger.warning("No GitHub App token for push re-review on %s", head_ref)
        return

    pr = await common._fetch_open_pr_for_branch(repo_config, head_ref, token=app_token)
    if not pr:
        common.logger.info(
            "Push ignored: repo=%s ref=%s reason=no open PR found for branch",
            repo_full_name,
            ref,
        )
        return

    # Push payloads normally carry repo privacy/id; fall back to PR metadata.
    # If the repo turns out public, re-scope the token so reviewer.py doesn't
    # proxy a full-installation token for a public PR.
    if repo_private is None:
        repo_private = common._repo_private_from_pr_metadata(pr)
        repo_id = repo_id or common._repo_id_from_pr_metadata(pr)
        if repo_private is False:
            app_token, app_token_expires_at = await common._reviewer_token_for_repo(
                repo_config,
                repo_private=repo_private,
                repo_id=repo_id,
            )
            if not app_token:
                common.logger.warning("No GitHub App token for push re-review on %s", head_ref)
                return
    pr_number = pr.get("number")
    pr_url = pr.get("html_url") or pr.get("url") or ""
    base_sha = pr.get("base", {}).get("sha", "")
    base_ref = pr.get("base", {}).get("ref", "")
    head_sha = after_sha
    pr_title = pr.get("title", "")
    if not isinstance(pr_number, int) or not base_sha:
        common.logger.warning(
            "Push to %s/%s head=%s ignored: PR metadata missing number/base SHA",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    thread_id = common.generate_reviewer_thread_id(
        repo_config["owner"], repo_config["name"], pr_number
    )
    try:
        metadata = await common._get_thread_metadata_safe(thread_id, raise_on_error=True)
    except Exception:
        common.logger.warning(
            "Push to %s/%s#%s head=%s dropped: could not read reviewer thread metadata",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
            exc_info=True,
        )
        return
    if metadata is None:
        common.logger.info(
            "Push to %s/%s#%s ignored: no reviewer thread for this PR. "
            "Trigger a first review (Slack `@open-swe review <url>` or request "
            "open-swe[bot] as a GitHub reviewer) to start watching.",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
        )
        return
    if metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        if metadata.get("watch") is True:
            common.logger.warning(
                "Push to %s/%s#%s head=%s dropped: reviewer thread kind metadata is invalid",
                repo_config["owner"],
                repo_config["name"],
                pr_number,
                head_sha,
            )
        else:
            common.logger.info(
                "Push to %s/%s#%s ignored: no reviewer thread for this PR",
                repo_config["owner"],
                repo_config["name"],
                pr_number,
            )
        return
    watch = metadata.get("watch")
    if watch is False:
        common.logger.info(
            "Push to %s ignored: reviewer thread %s is not watching", head_ref, thread_id
        )
        return
    if watch is not True:
        common.logger.warning(
            "Push to %s/%s#%s head=%s dropped: reviewer thread watch metadata is invalid",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
            head_sha,
        )
        return

    last_reviewed_sha = metadata.get("last_reviewed_sha")
    if isinstance(last_reviewed_sha, str) and last_reviewed_sha == head_sha:
        common.logger.info(
            "Push to %s ignored: head_sha unchanged from last_reviewed_sha", head_ref
        )
        return
    if (
        isinstance(last_reviewed_sha, str)
        and last_reviewed_sha
        and await common._is_pr_diff_unchanged_since_last_review(
            repo_config,
            base_ref=base_ref,
            last_reviewed_sha=last_reviewed_sha,
            head_sha=head_sha,
            token=app_token,
        )
    ):
        await common.set_reviewer_thread_metadata(thread_id, last_reviewed_sha=head_sha)
        # The old head's check disappears once the head moves (GitHub only
        # shows checks on the current head), so even though no re-review runs,
        # surface a settled check on the new head.
        unchanged_check_id = await common.create_review_check_run(
            owner=repo_config["owner"],
            repo=repo_config["name"],
            head_sha=head_sha,
            token=app_token,
            details_url=common.dashboard_thread_url(thread_id),
        )
        if unchanged_check_id is not None:
            await common.complete_review_check_run(
                owner=repo_config["owner"],
                repo=repo_config["name"],
                check_run_id=unchanged_check_id,
                token=app_token,
                conclusion="success",
                title="No new changes to review",
                summary=(
                    "The pull request diff is unchanged since the last reviewed "
                    f"commit {last_reviewed_sha}."
                ),
            )
        common.logger.info(
            "Push to %s ignored: PR diff unchanged since last reviewed SHA %s",
            head_ref,
            last_reviewed_sha,
        )
        return

    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common._ensure_thread_exists_for_metadata(thread_id, langgraph_client):
        return
    try:
        threads = await common.fetch_pr_review_threads(
            owner=repo_config["owner"],
            repo=repo_config["name"],
            pr_number=pr_number,
            token=app_token,
        )
        await common.reconcile_findings_with_review_threads(thread_id, threads)
    except Exception:
        common.logger.warning(
            "Could not sync review threads before push re-review for %s",
            thread_id,
            exc_info=True,
        )

    pr_meta: ReviewerPRMeta = {
        "owner": repo_config["owner"],
        "name": repo_config["name"],
        "number": pr_number,
        "url": pr_url,
        "title": pr_title,
        "head_ref": head_ref,
        "base_ref": base_ref,
        "author": (pr.get("user") or {}).get("login", ""),
    }
    await common.set_reviewer_thread_metadata(thread_id, pr=pr_meta, watch=True, head_sha=head_sha)

    # GitHub only shows check runs on a PR's current head commit, so the check
    # created on the previous head disappears after a follow-up push. Create a
    # fresh in-progress check on the new head SHA so the review stays visible;
    # publish (or the after-agent hook) settles this id.
    check_run_id = await common.create_review_check_run(
        owner=repo_config["owner"],
        repo=repo_config["name"],
        head_sha=head_sha,
        token=app_token,
        details_url=common.dashboard_thread_url(thread_id),
    )
    if check_run_id is not None:
        await common.set_reviewer_thread_metadata(
            thread_id, extra={"review_check_run_id": check_run_id}
        )

    re_review_prompt = (
        f"A new commit has been pushed to PR #{pr_number}. The new HEAD is "
        f"{head_sha}. Reconcile existing findings against the new diff, add any "
        f"net-new findings, and call `publish_review` once you're done."
    )
    configurable = common._build_reviewer_configurable(
        source="github_push",
        github_login=payload.get("sender", {}).get("login", "") or "",
        github_user_id=payload.get("sender", {}).get("id"),
        repo_config=repo_config,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=head_ref,
        repo_private=repo_private,
        re_review=True,
        last_reviewed_sha=last_reviewed_sha if isinstance(last_reviewed_sha, str) else "",
    )
    if check_run_id is not None:
        configurable["review_check_run_id"] = check_run_id
    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=True,
        finding_reply=False,
        explicit_request=False,
    )

    common.logger.info("Dispatching push re-review run for thread %s", thread_id)
    run = await common.dispatch_agent_run(
        thread_id,
        re_review_prompt,
        configurable,
        source="github_push",
        assistant_id=assistant_id,
        metadata=_review_run_metadata(check_run_id),
        client=langgraph_client,
    )
    await common._store_current_reviewer_run_id(thread_id, run)


_AUTOFIX_COMMAND_RE = re.compile(r"^[ \t]+autofix[ \t]+(on|off)\b", re.IGNORECASE)


def _parse_autofix_command(comment_body: str) -> bool | None:
    """Parse an explicitly mentioned ``@open-swe autofix on|off`` command."""
    mention = common.classify_comment_mention(comment_body, common.OPEN_SWE_TAGS)
    if mention.disposition != "accepted" or mention.end is None:
        return None
    remaining = comment_body[mention.end :]
    line_tail = remaining.splitlines()[0] if remaining else ""
    match = _AUTOFIX_COMMAND_RE.match(line_tail)
    if match is None:
        return None
    return match.group(1).lower() == "off"


async def process_github_autofix_command(payload: dict[str, Any], *, disabled: bool) -> None:
    """Persist and acknowledge a per-PR review auto-fix toggle."""
    repository = payload.get("repository", {})
    owner = repository.get("owner", {}).get("login", "")
    name = repository.get("name", "")
    pr_number = payload.get("issue", {}).get("number")
    if not owner or not name or not isinstance(pr_number, int):
        return

    await set_pr_autofix_disabled(owner, name, pr_number, disabled)
    common.logger.info(
        "Review auto-fix %s for %s/%s#%s via comment",
        "disabled" if disabled else "enabled",
        owner,
        name,
        pr_number,
    )

    comment = payload.get("comment", {})
    comment_id = comment.get("id")
    if not isinstance(comment_id, int):
        return
    token, _ = await common.get_github_app_installation_token_with_expiry(
        target_repo=f"{owner}/{name}", repositories=[name]
    )
    if not token:
        return
    try:
        await common.react_to_github_comment(
            {"owner": owner, "name": name},
            comment_id,
            event_type="issue_comment",
            token=token,
            pull_number=pr_number,
            node_id=comment.get("node_id"),
        )
    except Exception:  # noqa: BLE001
        common.logger.debug("Failed to react to auto-fix command comment", exc_info=True)


async def process_github_pr_comment(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub PR comment that tagged @open-swe.

    Retrieves the existing thread token, reacts with 👀, fetches all comments
    since the last @open-swe tag, then creates or queues a new run.

    Args:
        payload: The parsed GitHub webhook payload.
        event_type: One of 'issue_comment', 'pull_request_review_comment',
                    'pull_request_review'.
    """
    (
        repo_config,
        pr_number,
        branch_name,
        github_login,
        pr_url,
        comment_id,
        node_id,
    ) = await common.extract_pr_context(payload, event_type)
    target_repo = f"{repo_config.get('owner', '')}/{repo_config.get('name', '')}"
    github_user_id = payload.get("sender", {}).get("id")

    common.logger.info(
        "Processing GitHub PR comment: event=%s, pr=%s, branch=%s",
        event_type,
        pr_number,
        branch_name,
    )

    thread_id = common.get_thread_id_from_branch(branch_name) if branch_name else None
    if not thread_id:
        if not pr_number:
            common.logger.warning(
                "Could not determine thread_id for branch '%s' (no pr_number), skipping",
                branch_name,
            )
            return
        owner = repo_config.get("owner", "")
        name = repo_config.get("name", "")
        stable_key = f"{owner}/{name}/pr/{pr_number}"
        thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))
        common.logger.info(
            "Generated thread_id %s for non-open-swe branch '%s'", thread_id, branch_name
        )
        langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
        try:
            await langgraph_client.threads.update(thread_id, metadata={"branch_name": branch_name})
        except Exception as exc:  # noqa: BLE001
            if common._is_not_found_error(exc):
                await langgraph_client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"branch_name": branch_name},
                )
            else:
                common.logger.warning(
                    "Failed to persist branch_name metadata for thread %s", thread_id
                )

    github_token = await common._get_or_resolve_thread_github_token(thread_id, target_repo)

    if not github_token:
        common.logger.warning("No GitHub token for thread %s, skipping", thread_id)
        return

    if comment_id:
        try:
            await common.react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )
        except GitHubAuthError:
            github_token = await common._refresh_thread_github_token_after_401(
                thread_id, target_repo
            )
            if not github_token:
                common.logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
                return
            await common.react_to_github_comment(
                repo_config,
                comment_id,
                event_type=event_type,
                token=github_token,
                pull_number=pr_number,
                node_id=node_id,
            )

    if not pr_number:
        common.logger.warning("No PR number found in payload, skipping")
        return

    try:
        comments = await common.fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    except GitHubAuthError:
        github_token = await common._refresh_thread_github_token_after_401(thread_id, target_repo)
        if not github_token:
            common.logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
            return
        comments = await common.fetch_pr_comments_since_last_tag(
            repo_config, pr_number, token=github_token
        )
    if not comments:
        common.logger.info("No comments found since last @open-swe tag for PR %s", pr_number)
        return

    prompt = common.build_pr_prompt(comments, pr_url, repo_config=repo_config)
    await common._trigger_or_queue_run(
        thread_id,
        prompt,
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
    )


async def process_github_review_finding_reply(payload: dict[str, Any]) -> None:
    """Route replies to Open SWE review comments back to the reviewer graph."""
    parent_comment_id = common._review_comment_reply_parent_id(payload)
    if parent_comment_id is None:
        return

    sender = payload.get("sender", {})
    sender_login = sender.get("login") if isinstance(sender, dict) else None
    # Must match every identity we post as, not just the canonical name: a
    # reply we authored that is not recognised as ours is recorded as a human
    # reply and dispatched as a new reviewer run, which interrupts the review
    # that is still publishing — and that run replies again.
    if is_internal_bot_login(sender_login):
        return

    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    repo_private = common._repo_private_from_payload(payload)
    repo_id = common._repo_id_from_payload(payload)
    pr_number = pull_request.get("number")
    if not isinstance(pr_number, int):
        return

    thread_id = common.generate_reviewer_thread_id(
        repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
    )
    metadata = await common._get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        return

    app_token, app_token_expires_at = await common._reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        return

    threads = await common.fetch_pr_review_threads(
        owner=repo_config["owner"],
        repo=repo_config["name"],
        pr_number=pr_number,
        token=app_token,
    )
    await common.reconcile_findings_with_review_threads(thread_id, threads)
    findings = await common.list_reviewer_findings(thread_id)
    finding = next(
        (item for item in findings if parent_comment_id in common._finding_comment_ids(item)), None
    )
    if finding is None:
        return
    finding_id = finding.get("id")
    if not isinstance(finding_id, str):
        return

    comment = payload.get("comment", {})
    if not isinstance(comment, dict):
        return
    raw_reply_body = comment.get("body")
    reply_body = raw_reply_body if isinstance(raw_reply_body, str) else ""
    reply_author = sender_login if isinstance(sender_login, str) else "unknown"
    reply_comment_id = comment.get("id") if isinstance(comment.get("id"), int) else None
    raw_created_at = comment.get("created_at")
    interaction: FindingInteraction = {
        "kind": "human_reply",
        "github_comment_id": reply_comment_id,
        "github_parent_comment_id": parent_comment_id,
        "author": reply_author,
        "body": reply_body,
        "created_at": raw_created_at if isinstance(raw_created_at, str) else "",
        "needs_reassessment": True,
    }
    await common.append_finding_interaction(thread_id, finding_id, interaction)

    base_sha = pull_request.get("base", {}).get("sha", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    pr_url = pull_request.get("html_url", "") or pull_request.get("url", "")
    branch_name = pull_request.get("head", {}).get("ref", "")
    configurable = common._build_reviewer_configurable(
        source="github_review_comment",
        github_login=reply_author,
        github_user_id=sender.get("id") if isinstance(sender, dict) else None,
        repo_config=repo_config,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        branch_name=branch_name,
        repo_private=repo_private,
        re_review=True,
        reviewer_event="finding_reply",
    )
    configurable.update(
        {
            "finding_reply_id": finding_id,
            "finding_reply_author": reply_author,
            "finding_reply_body": reply_body,
        }
    )
    latest_metadata = await common._get_thread_metadata_safe(thread_id)
    review_in_flight = _full_review_in_flight_for_head(latest_metadata, head_sha)
    if review_in_flight:
        # Do not hand the review's check to this run: the review keeps it and
        # settles it itself; the reply runs afterwards and inherits nothing.
        inherited_check_run_id = None
        common.logger.info(
            "Enqueueing finding reply for %s behind the in-flight full review of head %s",
            thread_id,
            head_sha[:12],
        )
    else:
        inherited_check_run_id = await _settle_review_check_before_finding_reply(
            thread_id=thread_id,
            metadata=latest_metadata,
            owner=repo_config["owner"],
            repo=repo_config["name"],
            token=app_token,
        )
    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=bool(configurable.get("re_review")),
        finding_reply=configurable.get("reviewer_event") == "finding_reply",
        explicit_request=False,
    )
    finding_reply_prompt = common._build_queued_finding_reply_prompt(
        finding_id=finding_id,
        reply_author=reply_author,
        reply_body=reply_body,
        pr_number=pr_number,
    )
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    run = await common.dispatch_agent_run(
        thread_id,
        finding_reply_prompt,
        configurable,
        source="github_review_reply",
        assistant_id=assistant_id,
        metadata=_review_run_metadata(inherited_check_run_id),
        client=langgraph_client,
        multitask_strategy="enqueue" if review_in_flight else "interrupt",
    )
    # An enqueued reply must not become the thread's "current" run: the full
    # review still owns the check, and the completion handler settles a dying
    # review's check only while that review is current. The queued run owns
    # no check and inherits nothing, so it has nothing to lose by not being
    # recorded here.
    if not review_in_flight:
        await common._store_current_reviewer_run_id(thread_id, run)


async def process_github_issue(payload: dict[str, Any], event_type: str) -> None:
    """Process a GitHub issue or issue comment that tagged @open-swe."""
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    target_repo = f"{repo_config['owner']}/{repo_config['name']}"

    issue_id = str(issue.get("id", ""))
    issue_number = issue.get("number")
    github_login = payload.get("sender", {}).get("login", "")
    github_user_id = payload.get("sender", {}).get("id")
    issue_url = issue.get("html_url", "") or issue.get("url", "")
    title = issue.get("title", "No title")
    description = issue.get("body") or "No description"
    issue_author = issue.get("user", {}).get("login", "")

    common.logger.info(
        "Processing GitHub issue: event=%s, issue=%s, repo=%s/%s",
        event_type,
        issue_number,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    if not issue_id or not issue_number:
        common.logger.warning("Missing GitHub issue id/number, skipping")
        return

    thread_id = common.generate_thread_id_from_github_issue(issue_id)
    existing_thread = await common._thread_exists(thread_id)
    github_token = await common._get_or_resolve_thread_github_token(thread_id, target_repo)
    if not github_token:
        common.logger.warning("No GitHub App token for thread %s, skipping", thread_id)
        return
    comment = payload.get("comment", {})
    comment_id = comment.get("id")
    if event_type == "issue_comment" and comment_id:
        try:
            reacted = await common.react_to_github_comment(
                repo_config,
                comment_id,
                event_type="issue_comment",
                token=github_token,
            )
        except GitHubAuthError:
            github_token = await common._refresh_thread_github_token_after_401(
                thread_id, target_repo
            )
            if not github_token:
                common.logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
                return
            try:
                reacted = await common.react_to_github_comment(
                    repo_config,
                    comment_id,
                    event_type="issue_comment",
                    token=github_token,
                )
            except GitHubAuthError:
                common.logger.warning(
                    "Re-auth still produced 401 reacting to issue comment %s",
                    comment_id,
                )
                return
        if not reacted:
            common.logger.warning("Failed to react to GitHub issue comment %s", comment_id)

    if existing_thread:
        if event_type == "issue_comment":
            prompt = build_github_issue_followup_prompt(
                comment.get("user", {}).get("login", github_login) or github_login,
                comment.get("body", ""),
            )
        else:
            prompt = build_github_issue_update_prompt(github_login, title, description)
    else:
        try:
            comments = await common.fetch_issue_comments(
                repo_config, issue_number, token=github_token
            )
        except GitHubAuthError:
            github_token = await common._refresh_thread_github_token_after_401(
                thread_id, target_repo
            )
            if not github_token:
                common.logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
                return
            comments = await common.fetch_issue_comments(
                repo_config, issue_number, token=github_token
            )
        if comment_id and not any(item.get("comment_id") == comment_id for item in comments):
            comments.append(
                {
                    "body": comment.get("body", ""),
                    "author": comment.get("user", {}).get("login", "unknown"),
                    "created_at": comment.get("created_at", ""),
                    "comment_id": comment_id,
                }
            )
            comments.sort(key=lambda item: item.get("created_at", ""))

        prompt = build_github_issue_prompt(
            repo_config,
            issue_number,
            issue_id,
            title,
            description,
            comments,
            github_login=github_login,
            issue_author=issue_author,
            issue_url=issue_url,
        )
    configurable: dict[str, Any] = {
        "source": "github",
        "github_login": github_login,
        "github_user_id": github_user_id,
        "repo": repo_config,
        "github_issue": {
            "id": issue_id,
            "number": issue_number,
            "title": title,
            "url": issue_url,
        },
    }

    await common.upsert_agent_thread_owner_metadata(
        thread_id,
        source="github",
        repo_config=repo_config,
        github_login=github_login,
        title=title or (f"Issue #{issue_number}" if issue_number else ""),
        source_context={"github_issue": configurable["github_issue"]},
    )

    common.logger.info("Dispatching LangGraph run for thread %s from GitHub issue", thread_id)
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    await common.dispatch_agent_run(
        thread_id,
        prompt,
        configurable,
        source="github_issue",
        metadata=common._AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    common.logger.info("LangGraph run dispatched for thread %s from GitHub issue", thread_id)
