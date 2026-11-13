"""GitHub webhook handlers — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import httpx
import httpx2
from langgraph_sdk.errors import NotFoundError
from pydantic import AliasChoices, AliasPath, BaseModel, Field, JsonValue, ValidationError

from openswe import event_claims
from openswe.baby_sit import handle_ci_webhook
from openswe.dispatch import channel_of
from openswe.expedited_review.reviews import REVIEW_BODY_PREFIX
from openswe.github.comments import GitHubAuthError
from openswe.github.http import GitHubClient
from openswe.github.notifications import notify_slack_review
from openswe.github.pull_requests import PullRequest, PullRequestEvent, PullRequestPayload
from openswe.human_review.lifecycle import close_for_pull_request
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.settling import SettleTarget
from openswe.input_messages import (
    EnvelopeData,
    PersonIdentity,
    RunInput,
    SystemIdentity,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.review.approval_withdrawal import ApprovalWithdrawal
from openswe.review.approvals import (
    ApprovalPolicy,
    approval_mode_for,
    approval_policy_for_review,
)
from openswe.review.assessment_feedback import PublishedAssessment
from openswe.review.findings import (
    FindingInteraction,
    ReviewerPRMeta,
    ReviewerSlackThread,
    reviewer_thread_title,
)
from openswe.review.reconcile import reconcile_linked_findings
from openswe.review.shadow import ReviewShadow
from openswe.run_config import Repo, RunConfig
from openswe.slack.client import GitHubPrRef
from openswe.source_context import SourceContext
from openswe.thread_ids import (
    pr_comment_thread_id,
    reviewer_thread_id,
    thread_id_from_branch,
)
from openswe.threads import index, succession
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.users import User
from openswe.walkthrough.record import Walkthrough
from openswe.webhooks import common
from openswe.workspaces.routing import repo_is_routable


async def _trusted_authors(*logins: str, comments: Iterable[dict[str, Any]] = ()) -> frozenset[str]:
    """Lowercased logins, among the people in this event, who are known Open SWE users."""
    return await User.known_logins(
        [*logins, *(str(comment.get("author") or "") for comment in comments)]
    )


def build_github_pr_review_prompt(
    repo_config: dict[str, str],
    pr_number: int,
    pr_url: str,
    base_sha: str,
    head_sha: str,
) -> str:
    """Build the reviewer instruction text; PR metadata is serialized separately."""
    return prompt("runs/github-pr-review")


def _github_person(login: str, user_id: object = None) -> PersonIdentity:
    stable = str(user_id) if user_id not in (None, "") else login or "unknown"
    person: PersonIdentity = {"id": f"github:{stable}"}
    if login:
        person["display_name"] = login
        person["github_login"] = login
    return person


def _github_human_run_input(
    login: str,
    content: str,
    *,
    user_id: object = None,
    data: EnvelopeData | None = None,
) -> RunInput:
    person = _github_person(login, user_id)
    return {
        "messages": [
            human_input(
                content,
                {
                    "sender_id": person["id"],
                    "surface": "github",
                    "kind": "human",
                    "data": data or {},
                },
            ),
        ]
    }


def _github_webhook_run_input(content: str, *, data: EnvelopeData) -> RunInput:
    actor: SystemIdentity = {
        "id": "system:github-webhook",
        "display_name": "GitHub webhook",
        "platform": "github",
    }
    return {
        "messages": [
            system_introduction(actor),
            system_input(
                content,
                {
                    "sender_id": actor["id"],
                    "surface": "github",
                    "kind": "system",
                    "data": data,
                },
            ),
        ]
    }


def _pr_data(
    repo_config: dict[str, str], pr_number: int, pr_url: str, base_sha: str, head_sha: str
) -> EnvelopeData:
    return {
        "pull_request": {
            "repository": f"{repo_config.get('owner')}/{repo_config.get('name')}",
            "number": pr_number,
            "url": pr_url,
            "base_sha": base_sha,
            "head_sha": head_sha,
        }
    }


async def trigger_pr_review_from_ref(
    pr_ref: GitHubPrRef,
    *,
    source: str,
    github_login: str = "",
    github_user_id: int | None = None,
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
    allow_draft: bool = False,
) -> dict[str, Any]:
    repo_config = {"owner": pr_ref.owner, "name": pr_ref.repo}
    if not common.is_repo_allowed(repo_config):
        return {
            "success": False,
            "error": f"{pr_ref.owner}/{pr_ref.repo} is not in the repository allowlist",
        }

    # Full token to read PR metadata (privacy/id aren't in the trigger ref);
    # re-scoped below once we know whether the repo is public.
    app_token, app_token_expires_at = await common.get_github_app_installation_token_with_expiry()
    if not app_token:
        common.logger.warning("No GitHub App token available for PR reviewer request")
        return {"success": False, "error": "No GitHub App token available"}

    pr_metadata = await common.fetch_github_pr_metadata(pr_ref, token=app_token)
    if not pr_metadata:
        return {"success": False, "error": "Could not fetch pull request metadata"}

    if pr_metadata.get("draft") and not allow_draft:
        return {
            "success": False,
            "error": f"{pr_ref.url} is a draft. " + prompt("tools/human-review-blocked"),
        }

    repo_private = common.repo_private_from_pr_metadata(pr_metadata)
    repo_id = common.repo_id_from_pr_metadata(pr_metadata)
    app_token, app_token_expires_at = await common.reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        common.logger.warning("No GitHub App token available for PR reviewer request")
        return {"success": False, "error": "No GitHub App token available"}

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

    thread_id = await succession.succeed(
        reviewer_thread_id(pr_ref.owner, pr_ref.repo, pr_ref.number)
    )
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common.ensure_thread_exists_for_metadata(
        thread_id,
        langgraph_client,
        title=reviewer_thread_title({"number": pr_ref.number, "title": pr_title}),
    ):
        return {"success": False, "error": "Could not create reviewer thread"}

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
    slack_thread_meta: ReviewerSlackThread = {}
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

    review_prompt = build_github_pr_review_prompt(
        repo_config, pr_ref.number, pr_url, base_sha, head_sha
    )
    configurable = await common.build_reviewer_configurable(
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

    common.logger.info(
        "Dispatching reviewer run for thread %s from %s PR review request", thread_id, source
    )
    review_input = (
        _github_human_run_input(
            github_login,
            review_prompt,
            user_id=github_user_id,
            data=_pr_data(repo_config, pr_ref.number, pr_url, base_sha, head_sha),
        )
        if github_login
        else _github_webhook_run_input(
            review_prompt, data=_pr_data(repo_config, pr_ref.number, pr_url, base_sha, head_sha)
        )
    )
    run = await common.dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source=source,
        thread_title=None,
        input=review_input,
        assistant_id="reviewer",
        metadata=common.AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    await common.store_current_reviewer_run_id(thread_id, run)
    await ReviewShadow.follow(
        RunConfig.parse(configurable), kickoff=review_prompt, pr_title=pr_title
    )
    return {"success": True, "queued": False, "thread_id": thread_id, "pr_url": pr_url}


async def _dispatch_first_review_from_pr_payload(payload: dict[str, Any], *, source: str) -> None:
    """Trigger a first-review run on the canonical reviewer thread for a PR."""
    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    repo_private = common.repo_private_from_payload(payload)
    repo_id = common.repo_id_from_payload(payload)
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

    thread_id = await index.current_thread_id(
        reviewer_thread_id(repo_config.get("owner", ""), repo_config.get("name", ""), pr_number)
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
    if payload.get("action") == "ready_for_review":
        metadata = await common.get_thread_metadata_safe(thread_id)
        if metadata is not None and metadata.get("kind") == common.REVIEWER_THREAD_KIND:
            existing_last_reviewed_sha = metadata.get("last_reviewed_sha")
            if isinstance(existing_last_reviewed_sha, str) and existing_last_reviewed_sha:
                if existing_last_reviewed_sha == head_sha:
                    await common.set_reviewer_thread_metadata(thread_id, pr=pr_meta, watch=True)
                    assessment = await PublishedAssessment.withheld(
                        repo_config["owner"], repo_config["name"], pr_number, head_sha
                    )
                    if assessment is None:
                        common.logger.info(
                            "Skipping ready_for_review auto-review for %s/%s#%s: "
                            "head_sha unchanged from last_reviewed_sha",
                            repo_config.get("owner"),
                            repo_config.get("name"),
                            pr_number,
                        )
                        return
                    if review_id := await assessment.approve(reviewer_thread_id=thread_id):
                        await common.set_reviewer_thread_metadata(
                            thread_id, extra={"review_assessment_id": review_id}
                        )
                        return
                    # The draft's verdict may no longer hold; review the whole PR again.
                else:
                    last_reviewed_sha = existing_last_reviewed_sha

    app_token, app_token_expires_at = await common.reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        common.logger.warning("No GitHub App token available for reviewer dispatch")
        return

    thread_id = await succession.succeed(thread_id)
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common.ensure_thread_exists_for_metadata(
        thread_id,
        langgraph_client,
        title=reviewer_thread_title({"number": pr_number, "title": pr_title}),
    ):
        return

    await common.set_reviewer_thread_metadata(
        thread_id, pr=pr_meta, watch=True, head_sha=head_sha, extra={"review_base_sha": base_sha}
    )

    check_run_id = await common.create_review_check_run(
        owner=repo_config.get("owner", ""),
        repo=repo_config.get("name", ""),
        head_sha=head_sha,
        token=app_token,
        details_url=common.dashboard_thread_url(thread_id),
    )
    if check_run_id is not None:
        await common.track_review_check_run(
            thread_id,
            owner=repo_config.get("owner", ""),
            repo=repo_config.get("name", ""),
            token=app_token,
            check_run_id=check_run_id,
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
    configurable = await common.build_reviewer_configurable(
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

    common.logger.info("Dispatching reviewer run for thread %s (source=%s)", thread_id, source)
    run_input = _github_webhook_run_input(
        prompt, data=_pr_data(repo_config, pr_number, pr_url, base_sha, head_sha)
    )
    run = await common.dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source=source,
        thread_title=None,
        input=run_input,
        assistant_id="reviewer",
        metadata=common.AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    await common.store_current_reviewer_run_id(thread_id, run)
    common.logger.info("Reviewer run dispatched for thread %s (source=%s)", thread_id, source)
    await ReviewShadow.follow(RunConfig.parse(configurable), kickoff=prompt, pr_title=pr_title)


async def process_github_pr_walkthrough(payload: dict[str, object]) -> None:
    """Plan open PRs, including drafts, independently of automatic assessment."""
    from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
    from openswe.review_scout.launch import ReviewScoutTarget
    from openswe.workspaces.store import WORKSPACES

    event = PullRequestEvent.parse(payload)
    if event is None or event.identity is None:
        return
    owner, repo, number = event.identity
    pull = event.pull_request
    if pull.state == "closed" or not pull.head_sha or not pull.base_sha:
        return
    workspace = await WORKSPACES.owner_of_repo(f"{owner}/{repo}")
    settings = await cached_workspace_settings(workspace)
    if settings.get("implementation_walkthrough_enabled") is True:
        stored = await PullRequest.get(owner, repo, number)
        if stored is not None:
            for thread_id in stored.thread_ids:
                thread = await THREADS.get(thread_id)
                if thread is not None and thread.status == "busy":
                    return
        if (
            pull.head_ref
            and await index.ThreadSearch(
                metadata={
                    "implementation_pr_target": {
                        "repository": f"{owner}/{repo}",
                        "head": pull.head_ref,
                    }
                },
                status="busy",
                limit=1,
            ).run()
        ):
            return
    target = ReviewScoutTarget(
        owner=owner,
        repo=repo,
        pr_number=number,
        pr_title=pull.title,
        base_sha=pull.base_sha,
        head_sha=pull.head_sha,
        workspace_slug=workspace,
    )
    try:
        if await target.walkthrough() is None:
            await target.start()
    except Exception:
        common.logger.exception("Could not start the PR walkthrough", extra=target.log_extra)


async def process_github_pr_ready(payload: dict[str, Any]) -> None:
    """Auto-review a PR that has just been opened or marked ready-for-review.

    Drafts are gated by the PR author's ``review_draft_prs`` profile flag
    (with the workspace's setting as a fallback).
    """
    pull_request = payload.get("pull_request", {})
    is_draft = bool(pull_request.get("draft"))
    if is_draft:
        author = pull_request.get("user") or {}
        author_login = author.get("login", "") if isinstance(author, dict) else ""
        repository = payload.get("repository", {})
        draft_repo_config = {
            "owner": (repository.get("owner") or {}).get("login", ""),
            "name": repository.get("name", ""),
        }
        if not await common.draft_review_enabled_for_author(author_login, draft_repo_config):
            common.logger.info(
                "Skipping auto-review of draft PR by %s: review_draft_prs is disabled",
                author_login or "<unknown>",
            )
            return
    # Use source="github" so the reviewer resolver can use the GitHub App token;
    # "github_auto" would fall through to the email-based path, which has no
    # user_email to route on for webhook-triggered runs.
    await _dispatch_first_review_from_pr_payload(payload, source="github")


async def settle_human_review_on_close(payload: dict[str, Any]) -> None:
    """Mark a closed PR's open review card merged or closed, whoever closed the PR."""
    event = PullRequestEvent.parse(payload)
    identity = event.identity if event is not None else None
    if identity is None:
        return
    await close_for_pull_request(*identity)


async def withdraw_superseded_approvals(payload: dict[str, Any]) -> None:
    """Dismiss Open SWE's approvals of earlier commits once new commits reach a pull request."""
    event = PullRequestEvent.parse(payload)
    identity = event.identity if event is not None else None
    if identity is None:
        return
    await ApprovalWithdrawal(owner=identity[0], repo=identity[1], number=identity[2]).attempt()


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

    thread_id = await index.current_thread_id(
        reviewer_thread_id(repo_config.get("owner", ""), repo_config.get("name", ""), pr_number)
    )
    metadata = await common.get_thread_metadata_safe(thread_id)
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
        if await common.draft_review_enabled_for_author(author_login, repo_config):
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


class _ReviewHeadContext(BaseModel):
    owner: str = Field(
        validation_alias=AliasChoices(
            AliasPath("repository", "owner", "login"), AliasPath("repository", "owner", "name")
        )
    )
    name: str = Field(validation_alias=AliasPath("repository", "name"))
    private: bool | None = Field(None, validation_alias=AliasPath("repository", "private"))
    repo_id: int | None = Field(None, validation_alias=AliasPath("repository", "id"))
    actor: str = Field("", validation_alias=AliasPath("sender", "login"))
    actor_id: int | None = Field(None, validation_alias=AliasPath("sender", "id"))


class _ReviewPullRequest(PullRequestPayload):
    html_url: str = ""
    url: str = ""
    base_repo_private: bool | None = Field(
        None, validation_alias=AliasPath("base", "repo", "private")
    )
    base_repo_id: int | None = Field(None, validation_alias=AliasPath("base", "repo", "id"))


class _ForkReviewUpdate(BaseModel):
    head_repo_id: int = Field(validation_alias=AliasPath("pull_request", "head", "repo", "id"))
    base_repo_id: int = Field(validation_alias=AliasPath("repository", "id"))
    head_ref: str = Field(validation_alias=AliasPath("pull_request", "head", "ref"))
    head_sha: str = Field(validation_alias=AliasPath("pull_request", "head", "sha"))
    pull_request: dict[str, object]


async def process_github_pr_synchronize(payload: dict[str, object]) -> None:
    """Re-review fork heads in the upstream repository's watched reviewer thread."""
    try:
        update = _ForkReviewUpdate.model_validate(payload)
    except ValidationError:
        common.logger.warning("PR synchronize lacks fork head context", exc_info=True)
        return
    if update.head_repo_id == update.base_repo_id:
        return
    if await common.enforce_public_repo_org_gate(payload, "pull_request") is not None:
        return
    await _process_github_head_update(
        payload, head_ref=update.head_ref, head_sha=update.head_sha, pr=update.pull_request
    )


async def process_github_push_event(payload: dict[str, Any]) -> None:
    """Re-trigger the reviewer for a watched PR when its head branch is pushed to."""
    ref = payload.get("ref", "")
    after_sha = payload.get("after", "")
    if not ref.startswith("refs/heads/"):
        common.logger.debug("Push ignored: ref %s is not a branch", ref)
        return
    if not isinstance(after_sha, str) or not after_sha or set(after_sha) == {"0"}:
        common.logger.debug("Push to %s ignored: branch deletion or missing SHA", ref)
        return
    head_ref = ref[len("refs/heads/") :]

    await _process_github_head_update(payload, head_ref=head_ref, head_sha=after_sha)


async def _process_github_head_update(
    payload: dict[str, object],
    *,
    head_ref: str,
    head_sha: str,
    pr: dict[str, object] | None = None,
) -> None:
    """Dispatch a watched PR head through the normal assessment and publication path."""
    event_type = "pull_request" if pr is not None else "push"
    try:
        context = _ReviewHeadContext.model_validate(payload)
    except ValidationError:
        common.logger.warning("Review head update lacks repository context", exc_info=True)
        return
    repo_config = {"owner": context.owner, "name": context.name}
    repo_private = context.private
    repo_id = context.repo_id
    if not context.owner or not context.name or not head_sha or not head_ref:
        return
    if not await common.is_repo_auto_review_enabled(repo_config):
        common.logger.info(
            "Push to %s/%s head=%s ignored: automatic review disabled",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    app_token, app_token_expires_at = await common.reviewer_token_for_repo(
        repo_config,
        repo_private=repo_private,
        repo_id=repo_id,
    )
    if not app_token:
        common.logger.warning("No GitHub App token for push re-review on %s", head_ref)
        return

    if pr is None:
        pr = await common.fetch_open_pr_for_branch(repo_config, head_ref, token=app_token)
    if not pr:
        common.logger.debug(
            "No open PR found for push to %s/%s head=%s",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    try:
        pull_request = _ReviewPullRequest.model_validate(pr)
    except ValidationError:
        common.logger.warning("Review head update lacks PR context", exc_info=True)
        return

    # Push payloads normally carry repo privacy/id; fall back to PR metadata.
    # If the repo turns out public, re-scope the token so reviewer.py doesn't
    # proxy a full-installation token for a public PR.
    if repo_private is None:
        repo_private = pull_request.base_repo_private
        repo_id = repo_id or pull_request.base_repo_id
        if repo_private is False:
            app_token, app_token_expires_at = await common.reviewer_token_for_repo(
                repo_config,
                repo_private=repo_private,
                repo_id=repo_id,
            )
            if not app_token:
                common.logger.warning("No GitHub App token for push re-review on %s", head_ref)
                return
    pr_number = pull_request.number
    pr_url = pull_request.html_url or pull_request.url
    base_sha = pull_request.base_sha
    base_ref = pull_request.base_ref
    pr_title = pull_request.title
    if not isinstance(pr_number, int) or not base_sha or not head_sha:
        common.logger.warning(
            "Push to %s/%s head=%s ignored: PR metadata missing number/base/head SHA",
            repo_config["owner"],
            repo_config["name"],
            head_ref,
        )
        return

    thread_id = await index.current_thread_id(
        reviewer_thread_id(repo_config["owner"], repo_config["name"], pr_number)
    )
    metadata = await common.get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        common.logger.info(
            "Push to %s/%s#%s ignored: no reviewer thread for this PR. "
            "Trigger a first review (Slack `@Robin review <url>` or request "
            "open-swe[bot] as a GitHub reviewer) to start watching.",
            repo_config["owner"],
            repo_config["name"],
            pr_number,
        )
        return
    if not metadata.get("watch"):
        common.logger.info(
            "Push to %s ignored: reviewer thread %s is not watching", head_ref, thread_id
        )
        return

    if pull_request.state == "closed":
        return
    if pull_request.draft and not await common.draft_review_enabled_for_author(
        pull_request.author, repo_config
    ):
        return

    last_reviewed_sha = metadata.get("last_reviewed_sha")
    if isinstance(last_reviewed_sha, str) and last_reviewed_sha == head_sha:
        common.logger.info(
            "Push to %s ignored: head_sha unchanged from last_reviewed_sha", head_ref
        )
        return
    previous_assessment = await PublishedAssessment.latest(context.owner, context.name, pr_number)
    previous_base_sha = metadata.get("last_reviewed_base_sha")
    if (
        isinstance(last_reviewed_sha, str)
        and last_reviewed_sha
        and await common.is_pr_diff_unchanged_since_last_review(
            repo_config,
            base_ref=base_ref,
            base_sha=base_sha,
            last_reviewed_base_sha=previous_base_sha if isinstance(previous_base_sha, str) else "",
            last_reviewed_sha=last_reviewed_sha,
            head_sha=head_sha,
            token=app_token,
        )
        and not (
            previous_assessment is not None
            and previous_assessment.approved
            and await approval_mode_for(context.owner, context.name) == "approve"
            and (
                await approval_policy_for_review(
                    context.owner, context.name, pr_number, token=app_token
                )
                or ApprovalPolicy()
            ).configured
        )
    ):
        await PullRequest(
            owner=repo_config["owner"], repo=repo_config["name"], number=pr_number
        ).link_review(reviewer_thread_id=thread_id, head_sha=head_sha)
        if previous_assessment is not None and previous_assessment.head_sha == last_reviewed_sha:
            await previous_assessment.model_copy(update={"head_sha": head_sha}).save()
        await common.set_reviewer_thread_metadata(
            thread_id, last_reviewed_sha=head_sha, last_reviewed_base_sha=base_sha
        )
        try:
            await Walkthrough.carry_forward(
                repo_config["owner"],
                repo_config["name"],
                pr_number,
                from_sha=last_reviewed_sha,
                to_sha=head_sha,
            )
        except Exception:
            common.logger.warning(
                "Could not carry the review walkthrough forward",
                exc_info=True,
                extra={"pr_number": pr_number, "scout_head_sha": head_sha},
            )
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

    thread_id = await succession.succeed(thread_id)
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    if not await common.ensure_thread_exists_for_metadata(
        thread_id,
        langgraph_client,
        title=reviewer_thread_title({"number": pr_number, "title": pr_title}),
    ):
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
        "author": pull_request.author,
    }
    await common.set_reviewer_thread_metadata(
        thread_id, pr=pr_meta, watch=True, head_sha=head_sha, extra={"review_base_sha": base_sha}
    )

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
        await common.track_review_check_run(
            thread_id,
            owner=repo_config["owner"],
            repo=repo_config["name"],
            token=app_token,
            check_run_id=check_run_id,
        )

    re_review_prompt = (
        f"A new commit has been pushed to PR #{pr_number}. The new HEAD is "
        f"{head_sha}. Reconcile existing findings against the new diff, add any "
        f"net-new findings, and call `publish_review` once you're done."
    )
    configurable = await common.build_reviewer_configurable(
        source="github_push",
        github_login=context.actor,
        github_user_id=context.actor_id,
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

    common.logger.info("Dispatching push re-review run for thread %s", thread_id)
    run = await common.dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="github_push",
        thread_title=None,
        input=_github_webhook_run_input(
            re_review_prompt,
            data={
                **_pr_data(repo_config, pr_number, pr_url, base_sha, head_sha),
                "event": {"type": event_type, "actor": context.actor},
            },
        ),
        assistant_id="reviewer",
        metadata=common.AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    await common.store_current_reviewer_run_id(thread_id, run)
    await ReviewShadow.follow(
        RunConfig.parse(configurable), kickoff=re_review_prompt, pr_title=pr_title
    )


class PreviewReady:
    """A pull request's preview is deployed: a review that works for apps does that work
    now, on the commit the preview serves.

    A review runs as soon as a pull request opens or changes, usually before its preview is
    up, so apps whose review step browses the preview would otherwise miss it. The checks
    whose success means a preview serves its pull request's head, such as a deploy
    workflow's last job, are named in ``review_app_preview_checks``; a skipped one, as on a
    pull request that builds no preview, starts nothing. When one passes for an open pull
    request of a private repository Robin reviews automatically, whose head is still that
    commit and whose review works for an app that browses, Robin queues a reviewer run on
    the pull request's review thread that does only the apps' work (``reviewer_event``
    ``"preview_ready"``) and publishes only findings that work turns up. Each named check
    starts its own run for a commit, so a pull request with several previews gets one run
    once each is up.

    A queued job (``JOB``) starts the run and comes back to it. It reads what it started
    from the thread's runs, marked with the commit and check (``MARK``), so a retried job
    never starts a second one: any other reviewer run on the thread cancels a queued or
    running one, so the run starts again, up to ``MOST_RUNS`` times, until one finishes.
    A preview can deploy before Robin has reviewed the pull request, or while it's a
    draft, so the job looks again every ``WAIT``, up to ``MOST_WAITS`` times.

    GitHub doesn't send a missed completion again, so in a workspace that names preview
    checks, a pull request opening or changing also starts a watch on its head
    (``RECONCILE``): every ``WAIT`` until ``WINDOW`` after,
    it queues the job of each configured check that passed there, unless that commit's work
    for it is done (``CLAIMS``).
    """

    CLAIMS: Final = "review_apps_preview_done"
    JOB: Final = "review_apps_on_preview"
    # The run metadata naming the commit and check a run is for.
    MARK: Final = "review_apps_preview"
    # How long after queuing a run Robin looks at whether it finished.
    FOLLOW_UP: Final = timedelta(minutes=20)
    MOST_RUNS: Final = 3
    WAIT: Final = timedelta(minutes=5)
    MOST_WAITS: Final = 12
    RECONCILE: Final = "review_apps_preview_reconcile"
    WINDOW: Final = timedelta(hours=1)

    @staticmethod
    async def checks(repo_config: dict[str, str], *, named: str | None = None) -> frozenset[str]:
        """The repository's preview checks, or just ``named``, while its reviews work for
        apps and run automatically. The name is matched on cached settings before the
        database lookups, since most CI deliveries name another check."""
        from openswe.dashboard.workspace_settings_cache import cached_workspace_settings

        if not common.is_repo_allowed(repo_config):
            return frozenset()
        settings = await cached_workspace_settings(
            await common.workspace_for_repo_config(repo_config)
        )
        checks = settings.review_app_preview_checks if settings.review_app_releases else frozenset()
        if named is not None:
            checks &= {named}
        if (
            not checks
            or not await repo_is_routable(repo_config["owner"], repo_config["name"])
            or not await common.is_repo_auto_review_enabled(repo_config)
        ):
            return frozenset()
        return checks

    @staticmethod
    async def _watched(repo_config: dict[str, str]) -> bool:
        """Whether the repository's workspace names preview checks for its apps, read so a
        failure raises instead of reading as none; true when it can't be read."""
        from openswe.dashboard.workspace_settings import load_workspace_settings
        from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

        try:
            owner = await WORKSPACES.owner_of_repo(f"{repo_config['owner']}/{repo_config['name']}")
            settings = await load_workspace_settings(owner or DEFAULT_WORKSPACE_SLUG)
        except Exception:
            common.logger.warning("Reading preview checks failed; watching anyway", exc_info=True)
            return True
        return bool(settings.review_app_releases and settings.review_app_preview_checks)

    @classmethod
    async def run(cls, payload: Mapping[str, Any]) -> None:
        """A check finished: when it's one that means previews are deployed, queue the job
        for each pull request whose head it checked. A pull request opened or changed:
        watch its head (``reconcile``)."""
        repo = payload.get("repository")
        # Only reviews of private repositories work for apps (``ReviewApps.of``).
        if not isinstance(repo, dict) or repo.get("private") is not True:
            return
        owner = (repo.get("owner") or {}).get("login", "")
        name = repo.get("name", "")
        if not owner or not name:
            return
        repo_config = {"owner": owner, "name": name}
        repo_id = common.repo_id_from_payload(dict(payload))
        pull = payload.get("pull_request")
        if isinstance(pull, dict):
            number, head_sha = pull.get("number"), (pull.get("head") or {}).get("sha")
            if (
                isinstance(number, int)
                and isinstance(head_sha, str)
                and await cls._watched(repo_config)
            ):
                await queue.schedule(
                    cls.RECONCILE,
                    {
                        "owner": owner,
                        "repo": name,
                        "number": number,
                        "head_sha": head_sha,
                        "repo_id": repo_id,
                        "deadline": (datetime.now(UTC) + cls.WINDOW).isoformat(),
                    },
                    due_at=datetime.now(UTC) + cls.WAIT,
                    key=f"{cls.RECONCILE}:{owner}/{name}#{number}@{head_sha}".lower(),
                    replace=False,
                )
            return
        check = payload.get("check_run")
        if not isinstance(check, dict):
            return
        head_sha = check.get("head_sha")
        numbers = [
            pull["number"]
            for pull in check.get("pull_requests") or []
            if isinstance(pull, dict) and isinstance(pull.get("number"), int)
        ]
        check_name = check.get("name")
        if (
            check.get("conclusion") != "success"
            or not isinstance(check_name, str)
            or not isinstance(head_sha, str)
            or not numbers
            or not await cls.checks(repo_config, named=check_name)
        ):
            return
        for number in numbers:
            await cls.enqueue(owner, name, number, head_sha, check_name, repo_id)

    @staticmethod
    def _key(owner: str, repo: str, number: int, head_sha: str, check: str) -> str:
        return f"{owner}/{repo}#{number}@{head_sha}:{check}".lower()

    @classmethod
    async def enqueue(
        cls, owner: str, repo: str, number: int, head_sha: str, check: str, repo_id: JsonValue
    ) -> bool:
        """Queue the commit's job for ``check`` unless its work is done, keeping one already
        queued; whether the work is still to do."""
        key = cls._key(owner, repo, number, head_sha, check)
        if await event_claims.held(cls.CLAIMS, key):
            return False
        await queue.schedule(
            cls.JOB,
            {
                "owner": owner,
                "repo": repo,
                "number": number,
                "head_sha": head_sha,
                "check": check,
                "repo_private": True,
                "repo_id": repo_id,
            },
            key=f"{cls.JOB}:{key}",
            replace=False,
        )
        return True

    @classmethod
    async def reconcile(cls, job: Job) -> None:
        """The watch on a pull request's head. A look that fails waits for the next one
        instead of raising: the queue's retries would give up well inside ``WINDOW``."""
        owner, repo, number, head_sha, deadline = (
            job.payload.get(field) for field in ("owner", "repo", "number", "head_sha", "deadline")
        )
        if not (
            isinstance(owner, str)
            and isinstance(repo, str)
            and isinstance(number, int)
            and isinstance(head_sha, str)
            and isinstance(deadline, str)
        ):
            return
        again = True
        try:
            # None also while the settings can't be read, so the watch goes on.
            checks = await cls.checks({"owner": owner, "name": repo})
            if checks:
                async with GitHubClient.as_app(owner, repo) as github:
                    repository = github.repo(owner, repo)
                    pr = await repository.pull_request(number).pull()
                    if pr.get("state") != "open" or (pr.get("head") or {}).get("sha") != head_sha:
                        return
                    passed: set[str] = set()
                    for check in checks:
                        # By name, as a head has hundreds of checks; a rerun that replaced a
                        # success is what counts (``check_runs``).
                        runs = await repository.check_runs(head_sha, name=check)
                        if any(
                            run.get("name") == check and run.get("conclusion") == "success"
                            for run in runs
                        ):
                            passed.add(check)
                again = passed != checks
                for check in passed:
                    again |= await cls.enqueue(
                        owner, repo, number, head_sha, check, job.payload.get("repo_id")
                    )
        except Exception:
            common.logger.warning(
                "Looking for a pull request's deployed previews failed",
                extra={"repository": f"{owner}/{repo}", "pr_number": number},
                exc_info=True,
            )
        due = datetime.now(UTC) + cls.WAIT
        if again and due < datetime.fromisoformat(deadline):
            await queue.schedule(cls.RECONCILE, job.payload, due_at=due, key=job.key)

    @staticmethod
    async def stop_on_close(payload: Mapping[str, Any]) -> None:
        """A pull request closed: cancel its apps-only runs still queued or running. What one
        saves after the close is deleted as it lands (``ReviewApps.save``), and the job
        starts none again for a closed pull request."""
        event = PullRequestEvent.parse(payload)
        identity = event.identity if event is not None else None
        if identity is None:
            return
        thread_id = await index.current_thread_id(reviewer_thread_id(*identity))
        thread = await index.get(thread_id)
        if thread is None:
            return
        runs = RUNTIMES.runs_on(thread.runtime).client.runs
        for status in ("pending", "running"):
            for run in await runs.list(thread_id, status=status, limit=100):
                if (run.get("metadata") or {}).get("reviewer_event") == "preview_ready":
                    await runs.cancel(thread_id, run["run_id"])

    @classmethod
    async def start_job(cls, job: Job) -> None:
        """The queued job: start the commit's run for its check, or look at the one it
        started and start it again when another reviewer run cancelled it. Raises, so the
        queue retries, when GitHub or LangGraph fails."""
        payload = job.payload
        owner, repo, number, head_sha, check = (
            payload.get(field) for field in ("owner", "repo", "number", "head_sha", "check")
        )
        if not (
            isinstance(owner, str)
            and isinstance(repo, str)
            and isinstance(number, int)
            and isinstance(head_sha, str)
            and isinstance(check, str)
        ):
            return
        repo_config = {"owner": owner, "name": repo}
        repo_private = payload.get("repo_private")
        repo_private = repo_private if isinstance(repo_private, bool) else None
        repo_id = payload.get("repo_id")
        app_token, _ = await common.reviewer_token_for_repo(
            repo_config,
            repo_private=repo_private,
            repo_id=repo_id if isinstance(repo_id, int) else None,
        )
        if not app_token:
            raise RuntimeError("No GitHub token to read the pull request")
        pr_ref = GitHubPrRef(
            owner=owner,
            repo=repo,
            number=number,
            url=f"https://github.com/{owner}/{repo}/pull/{number}",
        )
        pr = await common.fetch_github_pr_metadata(pr_ref, token=app_token)
        if pr is None:
            raise RuntimeError("Could not read the pull request")
        if pr.get("state") != "open" or (pr.get("head") or {}).get("sha") != head_sha:
            # Closed, or pushed to since: that commit's preview is no longer current, even
            # while the job waits for a review.
            return
        thread_id = await index.current_thread_id(reviewer_thread_id(owner, repo, number))
        thread = await index.get(thread_id)
        metadata = await common.get_thread_metadata_safe(thread_id)
        if (
            thread is None
            or metadata is None
            or metadata.get("kind") != common.REVIEWER_THREAD_KIND
            or pr.get("draft")
        ):
            # Robin hasn't reviewed it yet, as when its preview deployed first, or it's a draft.
            await cls._wait(job)
            return
        mark = f"{head_sha}:{check}"
        runs = RUNTIMES.runs_on(thread.runtime).client.runs
        started = [
            run
            for run in await runs.list(thread_id, limit=50)
            if (run.get("metadata") or {}).get(cls.MARK) == mark
        ]
        if any(run.get("status") in ("pending", "running") for run in started):
            await cls._again(job, payload, cls.FOLLOW_UP)
            return
        # Recorded once the commit's work is over, so the watch doesn't queue it again.
        done = cls._key(owner, repo, number, head_sha, check)
        if any(run.get("status") == "success" for run in started) or len(started) >= (
            cls.MOST_RUNS
        ):
            await event_claims.claim(cls.CLAIMS, done, ttl=timedelta(days=7))
            return
        queued = await cls._start(
            thread_id, repo_config, number, pr, mark=mark, repo_private=repo_private
        )
        if queued is None:
            # No app the review works for browses.
            await event_claims.claim(cls.CLAIMS, done, ttl=timedelta(days=7))
            return
        latest = await common.fetch_github_pr_metadata(pr_ref, token=app_token)
        if latest is not None and latest.get("state") != "open":
            # It closed as the run was queued, so closing's own cancel (``stop_on_close``) may
            # have looked before the run was there. A run queued on a ``local`` thread's
            # successor is on Managed Deep Agents (``succession``).
            queued_thread, queued_run = queued
            on = runs if queued_thread == thread_id else RUNTIMES.mda.client.runs
            await on.cancel(queued_thread, queued_run)
            return
        await cls._again(job, payload, cls.FOLLOW_UP)

    @classmethod
    async def _wait(cls, job: Job) -> None:
        """Look again in ``WAIT``, until the job has waited ``MOST_WAITS`` times."""
        waits = job.payload.get("waits")
        waits = waits if isinstance(waits, int) else 0
        if waits < cls.MOST_WAITS:
            await cls._again(job, {**job.payload, "waits": waits + 1}, cls.WAIT)

    @classmethod
    async def _again(cls, job: Job, payload: Mapping[str, JsonValue], delay: timedelta) -> None:
        """Run the job again after ``delay``: queued again under its key, it replaces this one."""
        await queue.schedule(cls.JOB, dict(payload), due_at=datetime.now(UTC) + delay, key=job.key)

    @classmethod
    async def _start(
        cls,
        thread_id: str,
        repo_config: dict[str, str],
        number: int,
        pr: Mapping[str, Any],
        *,
        mark: str,
        repo_private: bool | None,
    ) -> tuple[str, str] | None:
        """Queue the commit's apps-only run on the review thread of ``pr``, open and ready at
        that commit: the thread and run it queued, or ``None`` when no app the review works
        for looks at the preview."""
        # Imported here: it loads the agent stack, which the web app never imports.
        from openswe.review_apps.apps import ReviewApps

        head_sha = (pr.get("head") or {}).get("sha", "")
        base_sha = (pr.get("base") or {}).get("sha", "")
        pr_url = (
            pr.get("html_url", "")
            or f"https://github.com/{repo_config['owner']}/{repo_config['name']}/pull/{number}"
        )
        configurable = await common.build_reviewer_configurable(
            source="github_preview",
            github_login="",
            github_user_id=None,
            repo_config=repo_config,
            pr_number=number,
            pr_url=pr_url,
            base_sha=base_sha,
            head_sha=head_sha,
            branch_name=(pr.get("head") or {}).get("ref", ""),
            repo_private=repo_private,
            # Not a re-review: with no reviewed commit to start from, the run's diff is the
            # pull request's own, from its merge base.
            re_review=False,
        )
        configurable["reviewer_event"] = "preview_ready"
        apps = await ReviewApps.of(RunConfig.parse(configurable))
        if apps is None or not apps.browses:
            # No app this review works for looks at the preview.
            return None
        thread_id = await succession.succeed(thread_id)
        # Not stored as the thread's current reviewer run: a review it queues behind still
        # owns the check and settles it if it fails.
        run = await common.dispatch_agent_run(
            thread_id,
            None,
            configurable,
            source="github_preview",
            thread_title=None,
            input=_github_webhook_run_input(
                prompt("reviewer/preview-ready-kickoff"),
                data=_pr_data(repo_config, number, pr_url, base_sha, head_sha),
            ),
            assistant_id="reviewer",
            # So the job finds what it started, and closing the pull request cancels it.
            metadata={
                **common.AGENT_VERSION_METADATA,
                "reviewer_event": "preview_ready",
                cls.MARK: mark,
            },
            # After a review in progress, never in place of it.
            multitask_strategy="enqueue",
        )
        run_id = run.get("run_id") if isinstance(run, dict) else None
        if not isinstance(run_id, str):
            raise RuntimeError("LangGraph returned no run id")
        return thread_id, run_id


async def process_github_ci_event(
    payload: dict[str, Any], event_type: str, delivery_id: str | None = None
) -> None:
    """Evaluate active baby-sit watches and review requests for a signed GitHub CI event."""
    await handle_ci_webhook(payload, event_type, delivery_id=delivery_id)
    if event_type == "status":
        await settle_human_reviews_for_status(payload)
    elif payload.get("action") == "completed":
        await settle_human_reviews(payload)


_UNTAGGED_PR_TRIGGER_EVENTS = frozenset(["issue_comment", "pull_request_review"])


class _GitHubAccount(BaseModel):
    login: str


class _GitHubRepository(BaseModel):
    name: str
    owner: _GitHubAccount


class _GitHubPrOrIssue(BaseModel):
    number: int
    state: str
    pull_request: dict[str, object] | None = None


class _UntaggedPrEvent(BaseModel):
    action: str
    sender: _GitHubAccount
    repository: _GitHubRepository
    pull_request: _GitHubPrOrIssue | None = None
    issue: _GitHubPrOrIssue | None = None


class _AuthoredItem(BaseModel):
    user: _GitHubAccount | None = None


class _PrAuthorEvent(BaseModel):
    pull_request: _AuthoredItem | None = None
    issue: _AuthoredItem | None = None

    @classmethod
    def author_of(cls, payload: dict[str, Any]) -> str:
        """The PR author a comment webhook names, or ``""`` when it names none."""
        try:
            event = cls.model_validate(payload)
        except ValidationError:
            common.logger.info("GitHub comment payload has no readable PR author", exc_info=True)
            return ""
        item = event.pull_request or event.issue
        return item.user.login if item is not None and item.user is not None else ""


async def is_accepted_commenter(login: str) -> bool:
    """Only registered Open SWE users may prompt the agent from GitHub comments."""
    return bool(await User.known_logins([login]))


async def untagged_agent_pr_thread_id(payload: dict[str, Any], event_type: str) -> str | None:
    """The agent thread an untagged comment or review on a PR it opened should wake."""
    if event_type not in _UNTAGGED_PR_TRIGGER_EVENTS:
        return None
    try:
        event = _UntaggedPrEvent.model_validate(payload)
    except ValidationError:
        common.logger.info(
            "Ignoring untagged GitHub event with an unexpected payload shape",
            extra={"github_event": event_type},
            exc_info=True,
        )
        return None
    if event.action not in common.SUPPORTED_GH_COMMENT_ACTIONS[event_type]:
        return None
    target = event.pull_request if event_type == "pull_request_review" else event.issue
    if target is None or target.state != "open":
        return None
    if event_type == "issue_comment" and target.pull_request is None:
        return None
    pull_request = await PullRequest.get(
        event.repository.owner.login, event.repository.name, target.number
    )
    return pull_request.agent_thread_id if pull_request is not None else None


#: How many of a channel conversation's latest messages a PR thread starts with.
CHANNEL_HISTORY_MESSAGES = 50
#: PR thread metadata naming the channel conversation it continues.
CONTINUES_THREAD_KEY = "continues_thread"
#: Who may read and prompt a channel's thread, which the PR thread continuing it keeps.
_INHERITED_ACCESS_KEYS: Final = ("visibility", "owner_type", "owner_login")


def _state_messages(state: object) -> list[dict[str, JsonValue]]:
    values = state.get("values") if isinstance(state, Mapping) else None
    messages = values.get("messages") if isinstance(values, Mapping) else None
    return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []


def _starts_turn(messages: list[dict[str, JsonValue]], index: int) -> bool:
    """Whether a copy may start at ``index``: not at a tool result, nor between an AI
    message's tool calls and their results."""
    if messages[index].get("type") == "tool":
        return False
    previous = messages[index - 1] if index else {}
    return not (previous.get("type") == "ai" and previous.get("tool_calls"))


def recent_turns(messages: list[dict[str, JsonValue]], limit: int) -> list[dict[str, JsonValue]]:
    """The latest ``limit`` messages at most, starting at a turn boundary."""
    start = max(len(messages) - limit, 0)
    while start < len(messages) and not _starts_turn(messages, start):
        start += 1
    return messages[start:]


async def _channel_history(
    channel_thread_id: str, pr_thread_id: str, *, new: bool = False
) -> list[dict[str, JsonValue]]:
    """The channel conversation a PR thread continues, for the PR thread's first run.

    ``new``: the PR thread was just indexed as MDA's GitHub channel's, which creates
    it with its first run, so it has no messages to read yet.
    """
    try:
        if not new and _state_messages(await THREADS.state(pr_thread_id)):
            return []
        messages = _state_messages(await THREADS.state(channel_thread_id))
    except Exception:  # noqa: BLE001 - the review still runs, without the conversation
        common.logger.warning(
            "Could not read the channel conversation a PR thread continues",
            extra={"thread_id": pr_thread_id, "continues_thread": channel_thread_id},
            exc_info=True,
        )
        return []
    return recent_turns(messages, CHANNEL_HISTORY_MESSAGES)


#: MDA's GitHub channel, as thread metadata names it (``CHANNEL_METADATA_KEY``).
GITHUB_CHANNEL: Final = "github"


class PrCommentChannel(Protocol):
    """MDA's GitHub channel, which starts a comment's run on the pull request's own thread."""

    async def adopt(self, thread_id: str, metadata: dict[str, JsonValue]) -> None:
        """Index the thread as the channel's, before its metadata is written."""
        ...

    async def start(self, thread_id: str, configurable: dict[str, object], run_input: Any) -> bool:
        """Sign the run for the channel to start; whether it starts."""
        ...

    async def once(self) -> bool:
        """Whether a run the backend starts itself still starts: once per delivery."""
        ...


async def _channel_takes(channel: PrCommentChannel | None, thread_id: str) -> bool:
    """Whether the channel starts the run on the PR's own thread: it is new, or the channel's.

    One the backend made before the channel started these runs stays the backend's.
    """
    if channel is None:
        return False
    thread = await index.get(thread_id)
    return thread is None or channel_of(thread.metadata) == GITHUB_CHANNEL


async def process_github_pr_comment(
    payload: dict[str, Any],
    event_type: str,
    *,
    agent_thread_id: str | None = None,
    channel: PrCommentChannel | None = None,
) -> None:
    """Process a GitHub PR comment that tagged @open-swe.

    Retrieves the existing thread token, reacts with 👀, fetches all comments
    since the last @open-swe tag, then creates or queues a new run.

    Args:
        payload: The parsed GitHub webhook payload.
        event_type: One of 'issue_comment', 'pull_request_review_comment',
                    'pull_request_review'.
        agent_thread_id: The thread of the agent that opened the pull request,
            for a comment that doesn't mention Open SWE.
        channel: MDA's GitHub channel: it starts the run when it goes to the pull
            request's own thread, new or the channel's; the backend starts any
            other once per delivery.
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
    github_user_id = payload.get("sender", {}).get("id")

    common.logger.info(
        "Processing GitHub PR comment: event=%s, pr=%s, branch=%s",
        event_type,
        pr_number,
        branch_name,
    )

    own_thread_id = agent_thread_id or (thread_id_from_branch(branch_name) if branch_name else None)
    if own_thread_id:
        derived_thread_id = own_thread_id
    elif pr_number:
        derived_thread_id = pr_comment_thread_id(
            repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
        )
    else:
        common.logger.warning(
            "Could not determine thread_id for branch '%s' (no pr_number), skipping",
            branch_name,
        )
        return
    thread_id = await index.live_thread_id(derived_thread_id)
    current_thread_id = await index.current_thread_id(derived_thread_id)
    pr_thread_id = (
        pr_comment_thread_id(repo_config.get("owner", ""), repo_config.get("name", ""), pr_number)
        if pr_number
        else None
    )
    on_channel = thread_id == pr_thread_id and await _channel_takes(channel, thread_id)
    new_on_channel = on_channel and await index.get(thread_id) is None
    if agent_thread_id is not None:
        agent_metadata = await common.get_thread_metadata_safe(current_thread_id)
        if agent_metadata is None:
            common.logger.info(
                "Ignoring untagged PR comment for a missing agent thread",
                extra={"thread_id": thread_id, "pr_number": pr_number},
            )
            return
        if common.thread_is_private(agent_metadata) and not common.thread_is_promptable(
            agent_metadata, github_login
        ):
            return
    email = await User.email_for_login(github_login) or ""
    if email:
        thread_metadata = await common.authorize_github_thread(current_thread_id, github_login)
        github_token = await common.get_or_resolve_thread_github_token(thread_id, email)
    else:
        common.logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return

    if not github_token:
        common.logger.warning("No GitHub token for thread %s, skipping", thread_id)
        return

    if not pr_number:
        common.logger.warning("No PR number found in payload, skipping")
        return

    event = payload.get("review" if event_type == "pull_request_review" else "comment", {})
    event_body = event.get("body") or ""
    if event_type == "pull_request_review" and not event_body:
        event_body = f"_Submitted a review: {event.get('state', 'commented')}_"
    event_comment = {
        "body": event_body,
        "author": event.get("user", {}).get("login", ""),
        "created_at": event.get("submitted_at") or event.get("created_at", ""),
        "event_at": event.get("updated_at") if payload.get("action") == "edited" else None,
        "type": {
            "issue_comment": "pr_comment",
            "pull_request_review_comment": "review_comment",
            "pull_request_review": "review",
        }[event_type],
        "comment_id": comment_id,
        "path": event.get("path", ""),
        "line": event.get("line") or event.get("original_line"),
    }
    if not event_comment["created_at"] or not comment_id:
        return
    # The card's own vote already woke the agent and shows the approval.
    if (
        event_type == "pull_request_review"
        and payload.get("action") == "submitted"
        and str(event.get("state") or "").lower() == "approved"
        and event_body.startswith(REVIEW_BODY_PREFIX)
        and await HumanReviewRequest.is_expedited_approver(
            repo_config["owner"], repo_config["name"], pr_number, event_comment["author"]
        )
    ):
        return
    if common.thread_is_private(thread_metadata) and not common.thread_is_promptable(
        thread_metadata, event_comment["author"]
    ):
        return

    fetch_comments = (
        common.fetch_pr_comments_since_last_tag
        if agent_thread_id is None
        else common.fetch_pr_event_comments
    )
    try:
        comments = await fetch_comments(
            repo_config,
            pr_number,
            token=github_token,
            event_comment=event_comment,
            authorized_login=github_login if common.thread_is_private(thread_metadata) else None,
        )
    except GitHubAuthError:
        github_token = await common.refresh_thread_github_token_after_401(thread_id, email)
        if not github_token:
            common.logger.warning("Re-auth failed for thread %s after 401; skipping", thread_id)
            return
        comments = await fetch_comments(
            repo_config,
            pr_number,
            token=github_token,
            event_comment=event_comment,
            authorized_login=github_login if common.thread_is_private(thread_metadata) else None,
        )
    if not comments:
        common.logger.info("No comments found since last @open-swe tag for PR %s", pr_number)
        return

    if (
        agent_thread_id is not None
        and event_type == "pull_request_review"
        and event.get("state", "").lower() == "approved"
        and not (event.get("body") or "").strip()
        and not any(item.get("type") == "review_comment" for item in comments)
    ):
        return

    thread_id = await succession.succeed(derived_thread_id)
    if on_channel and channel is not None:
        await channel.adopt(thread_id, {"branch_name": branch_name})
    # The agent's own thread exists; a PR thread or a successor may not yet.
    elif thread_id != own_thread_id:
        common.logger.info("Using thread_id %s for branch '%s'", thread_id, branch_name)
        if await THREADS.get(thread_id) is None:
            await THREADS.create(
                thread_id, title=f"PR #{pr_number}", metadata={"branch_name": branch_name}
            )
        else:
            await THREADS.update(thread_id, {"branch_name": branch_name})

    # A channel (Linear, GitHub issues) starts the runs on its own threads, so
    # feedback on a PR its conversation opened continues on the PR's own thread.
    channel_metadata = None if on_channel else await common.get_thread_metadata_safe(thread_id)
    continues_thread: str | None = None
    if channel_metadata is not None and channel_of(channel_metadata):
        if not pr_number:
            return
        # The PR thread starts with the conversation's history, so only who may prompt
        # the conversation continues it, as when its own thread took the feedback.
        if common.thread_is_private(channel_metadata) and not common.thread_is_promptable(
            channel_metadata, github_login
        ):
            return
        continues_thread = thread_id
        thread_id = pr_comment_thread_id(
            repo_config.get("owner", ""), repo_config.get("name", ""), pr_number
        )
        # It also keeps the conversation's visibility and owner, so a private
        # conversation's history stays as private on the PR's thread.
        inherited = {
            key: channel_metadata[key] for key in _INHERITED_ACCESS_KEYS if key in channel_metadata
        }
        metadata: dict[str, JsonValue] = {
            "branch_name": branch_name,
            CONTINUES_THREAD_KEY: continues_thread,
            **inherited,
        }
        on_channel = await _channel_takes(channel, thread_id)
        if on_channel and channel is not None:
            new_on_channel = await index.get(thread_id) is None
            await channel.adopt(thread_id, metadata)
        else:
            await THREADS.create(thread_id, title=f"PR #{pr_number}", metadata=metadata)

    repo = Repo.parse(repo_config)
    if pr_number and repo is not None and repo.owner and repo.name:
        try:
            await PullRequest(owner=repo.owner, repo=repo.name, number=pr_number).link_thread(
                thread_id, source="github_pr_comment"
            )
        except Exception:  # noqa: BLE001
            common.logger.warning(
                "Failed to link PR comment thread to its pull request",
                extra={"pr_repo_full_name": f"{repo.owner}/{repo.name}", "pr_number": pr_number},
                exc_info=True,
            )

    trusted = await _trusted_authors(github_login, comments=comments)
    prompt = common.build_pr_prompt(comments, pr_url, repo_config=repo_config, trusted=trusted)
    pr_author = _PrAuthorEvent.author_of(payload)
    author_logins: set[str] = set()
    if repo is not None:
        try:
            stored = await PullRequest.get(repo.owner, repo.name, pr_number)
            pull_request = (
                stored
                if stored is not None and stored.author
                else PullRequest(
                    owner=repo.owner, repo=repo.name, number=pr_number, author=pr_author
                )
            )
            pr_author = pull_request.author
            for login in {str(item.get("author") or "") for item in comments}:
                if login and await pull_request.is_authored_by(login):
                    author_logins.add(login)
        except Exception:  # noqa: BLE001
            common.logger.warning(
                "Failed to resolve the PR author for GitHub comments",
                extra={"pr_repo_full_name": f"{repo.owner}/{repo.name}", "pr_number": pr_number},
                exc_info=True,
            )
    messages = []
    introduced: set[str] = {_github_person(github_login, github_user_id)["id"]}
    for item in comments:
        author = str(item.get("author") or "unknown")
        person = _github_person(author, github_user_id if author == github_login else None)
        if person["id"] not in introduced:
            messages.append(person_introduction(person))
            introduced.add(person["id"])
        messages.append(
            human_input(
                common.format_github_comment_body_for_prompt(
                    author, str(item.get("body", "")), trusted=trusted
                ),
                {
                    "sender_id": person["id"],
                    "surface": "github",
                    "kind": "human",
                    "data": {
                        "pull_request": {"number": pr_number, "url": pr_url, "author": pr_author},
                        "sender_is_pr_author": str(author in author_logins).lower(),
                        "comment_type": str(item.get("type", "comment")),
                        "path": str(item.get("path", "")),
                        "line": str(item.get("line", "")),
                        "created_at": str(item.get("created_at", "")),
                    },
                },
            )
        )
    if continues_thread is not None:
        messages = [
            *await _channel_history(continues_thread, thread_id, new=new_on_channel),
            *messages,
        ]
    if channel is not None and not on_channel and not await channel.once():
        return
    dispatched = await common.trigger_or_queue_run(
        thread_id,
        prompt,
        input={"messages": messages},
        github_login=github_login,
        github_user_id=github_user_id,
        repo_config=repo_config,
        pr_number=pr_number,
        token_repositories=common.event_thread_token_repositories(repo_config, payload),
        start=channel.start if on_channel and channel is not None else None,
    )
    if not dispatched:
        return
    # Only once the run is on its way, so a failure never shows 👀 and then nothing.
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
            github_token = await common.refresh_thread_github_token_after_401(thread_id, email)
            if github_token:
                await common.react_to_github_comment(
                    repo_config,
                    comment_id,
                    event_type=event_type,
                    token=github_token,
                    pull_number=pr_number,
                    node_id=node_id,
                )
            else:
                common.logger.warning("Re-auth failed for thread %s after 401", thread_id)

    if event_type == "pull_request_review":
        await notify_slack_review(
            thread_id,
            reviewer=event_comment["author"],
            review_url=f"{pr_url}#pullrequestreview-{comment_id}",
            pr_label=f"{repo_config['owner']}/{repo_config['name']}#{pr_number}",
            review_state=str(event.get("state") or ""),
            edited_body=event_body if payload.get("action") == "edited" else None,
            edited_at=event_comment["event_at"],
        )


async def process_github_review_thread(payload: dict[str, object]) -> None:
    """Re-read thread states so delayed resolution events cannot overwrite newer ones."""
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(pull_request, dict):
        return
    owner_data = repository.get("owner")
    owner = owner_data.get("login") if isinstance(owner_data, dict) else None
    repo = repository.get("name")
    number = pull_request.get("number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    thread_id = reviewer_thread_id(owner, repo, number)
    metadata = await common.get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        return
    async with GitHubClient.as_app(owner, repo) as github:
        await reconcile_linked_findings(thread_id, github.repo(owner, repo).pull_request(number))


async def process_github_review_finding_reply(payload: dict[str, Any]) -> None:
    """Route replies to Open SWE review comments back to the reviewer graph."""
    parent_comment_id = common.review_comment_reply_parent_id(payload)
    if parent_comment_id is None:
        return

    sender = payload.get("sender", {})
    sender_login = sender.get("login") if isinstance(sender, dict) else None
    if sender_login == "open-swe[bot]":
        return

    repo = payload.get("repository", {})
    pull_request = payload.get("pull_request", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    repo_private = common.repo_private_from_payload(payload)
    repo_id = common.repo_id_from_payload(payload)
    pr_number = pull_request.get("number")
    if not isinstance(pr_number, int):
        return

    thread_id = await index.current_thread_id(
        reviewer_thread_id(repo_config.get("owner", ""), repo_config.get("name", ""), pr_number)
    )
    metadata = await common.get_thread_metadata_safe(thread_id)
    if metadata is None or metadata.get("kind") != common.REVIEWER_THREAD_KIND:
        return

    app_token, app_token_expires_at = await common.reviewer_token_for_repo(
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
        (item for item in findings if parent_comment_id in common.finding_comment_ids(item)), None
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

    thread_id = await succession.succeed(thread_id)
    base_sha = pull_request.get("base", {}).get("sha", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    pr_url = pull_request.get("html_url", "") or pull_request.get("url", "")
    branch_name = pull_request.get("head", {}).get("ref", "")
    configurable = await common.build_reviewer_configurable(
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
    )
    configurable.update(
        {
            "reviewer_event": "finding_reply",
            "finding_reply_id": finding_id,
            "finding_reply_author": reply_author,
            "finding_reply_body": reply_body,
        }
    )
    finding_reply_prompt = common.build_queued_finding_reply_prompt(
        finding_id=finding_id,
        reply_author=reply_author,
        reply_body=reply_body,
        pr_number=pr_number,
    )
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    run = await common.dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source="github_review_reply",
        thread_title=None,
        input=_github_human_run_input(
            reply_author,
            finding_reply_prompt,
            user_id=sender.get("id") if isinstance(sender, dict) else None,
            data={
                "pull_request": {"number": pr_number, "url": pr_url},
                "finding_id": finding_id,
                "comment_id": reply_comment_id or "",
            },
        ),
        assistant_id="reviewer",
        metadata=common.AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    await common.store_current_reviewer_run_id(thread_id, run)


async def conversation_started(thread_id: str) -> bool:
    """Whether a run on the issue's thread already took the issue in.

    Not whether the thread is indexed: ``adopt`` indexes it before its first run
    starts, so a retried delivery or a start that failed would read as a follow-up
    and the first run would never see the issue.
    """
    try:
        state = await THREADS.state(thread_id)
    except NotFoundError:
        # Indexed, but no run created it on MDA yet.
        return False
    except (httpx.HTTPStatusError, httpx2.HTTPStatusError) as error:
        # A channel's thread is read over HTTP, which says so with a 404.
        if error.response.status_code == httpx.codes.NOT_FOUND:
            return False
        raise
    return bool(_state_messages(state))


async def prepare_github_issue_run(
    payload: dict[str, Any],
    thread_id: str,
    *,
    workspace: str | None,
    adopt: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    """Index the thread an issue mention starts its run on, and the run's configurable.

    MDA's GitHub channel writes the run's input; this checks the issue and its
    sender, and writes the thread's metadata. ``thread_id`` is the issue's thread,
    which the channel owns. ``adopt`` indexes it as the channel's before its
    metadata is written, so the write doesn't create it. ``workspace`` is the one
    the thread started in, for a follow-up; ``None`` for the repository's own.
    ``None`` when the run can't start.
    """
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    repo_config = {
        "owner": repo.get("owner", {}).get("login", ""),
        "name": repo.get("name", ""),
    }
    issue_id = str(issue.get("id", ""))
    issue_number = issue.get("number")
    github_login = payload.get("sender", {}).get("login", "")
    if not issue_id or not issue_number:
        common.logger.warning("Missing GitHub issue id/number, skipping")
        return None
    if not await User.email_for_login(github_login):
        common.logger.warning("No email mapping for GitHub user '%s', skipping", github_login)
        return None
    # The thread's App token, cached for the backend's own GitHub calls on it.
    await common.get_or_resolve_thread_github_token(thread_id, "")
    workspace = workspace or await common.workspace_for_repo_config(repo_config)
    title = issue.get("title", "No title")
    github_issue = {
        "id": issue_id,
        "number": issue_number,
        "title": title,
        "url": issue.get("html_url", "") or issue.get("url", ""),
    }
    token_repositories = common.event_thread_token_repositories(repo_config, payload)
    if adopt is not None:
        await adopt(thread_id)
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="github",
        repo_config=repo_config,
        github_login=github_login,
        title=title or f"Issue #{issue_number}",
        source_context=SourceContext.parse({"github_issue": github_issue}),
        workspace=workspace,
        token_repositories=token_repositories,
    )
    if not persisted and token_repositories is not None:
        common.logger.error(
            "Not starting a GitHub issue run whose token scope could not be recorded",
            extra={"agent_thread_id": thread_id},
        )
        return None
    return {
        "source": "github",
        "github_login": github_login,
        "github_user_id": payload.get("sender", {}).get("id"),
        "repo": repo_config,
        "github_issue": github_issue,
        "workspace": workspace,
        "environment": workspace,
    }


class _PullRequestNumber(BaseModel):
    number: int


class _CiRun(BaseModel):
    pull_requests: list[_PullRequestNumber] = []


class _HumanReviewEvent(BaseModel):
    """The slice of a ``pull_request_review`` or CI webhook that names its pull requests."""

    repository: _GitHubRepository
    pull_request: _PullRequestNumber | None = None
    check_suite: _CiRun | None = None
    check_run: _CiRun | None = None
    workflow_run: _CiRun | None = None

    @property
    def numbers(self) -> set[int]:
        numbers = {self.pull_request.number} if self.pull_request is not None else set()
        for run in (self.check_suite, self.check_run, self.workflow_run):
            if run is not None:
                numbers.update(pr.number for pr in run.pull_requests)
        return numbers


async def settle_human_reviews(payload: dict[str, Any]) -> None:
    """Re-check the open standard review requests of the pull requests an event names."""
    try:
        event = _HumanReviewEvent.model_validate(payload)
    except ValidationError:
        common.logger.info("GitHub event names no pull requests for human review", exc_info=True)
        return
    owner, repo = event.repository.owner.login, event.repository.name
    for number in event.numbers:
        await SettleTarget.queue(owner, repo, number)


class _StatusEvent(BaseModel):
    repository: _GitHubRepository
    state: str


async def settle_human_reviews_for_status(payload: dict[str, Any]) -> None:
    """A finished commit status names only a SHA, so re-check the repository's open requests."""
    try:
        event = _StatusEvent.model_validate(payload)
    except ValidationError:
        common.logger.info("GitHub status event has an unexpected shape", exc_info=True)
        return
    if event.state == "pending":
        return
    await SettleTarget.queue(event.repository.owner.login, event.repository.name)
