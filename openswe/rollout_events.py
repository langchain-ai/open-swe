"""Accept a deployment event after an environment has finished syncing.

The route trusts a GitHub Actions OIDC token instead of a shared secret. A
verified deploy is written to the event log so a thread that was asked for
a rollout check can wake through ``listen_events`` after its pull request merges.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict
from uuid import UUID

import httpx2
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, JsonValue

from openswe.database import configured
from openswe.dispatch import dispatch_agent_run
from openswe.federation.github_oidc import GitHubActionsClaims, InvalidFederatedToken
from openswe.federation.github_oidc import verify as verify_github_oidc
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.repositories import Repository
from openswe.prompts import prompt
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.webhooks.event_log import EventLog, EventRefs
from openswe.webhooks.event_subscriptions import EventSubscription
from openswe.workspaces.routing import workspace_for_repo
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

router = APIRouter()


class RolloutAccepted(TypedDict):
    status: Literal["accepted"]
    target: str
    commits: int


_AUDIENCE = "openswe-rollout"
ROLLOUT_CHECK_REQUESTED = "rollout_check"
MERGE_COMMIT_SHA = "merge_commit_sha"
MERGE_COMMIT_PR_URL = "merge_commit_pr_url"
ROLLOUT_CHECK_CONTEXT = "rollout_check_context"
_DEPLOYED = "deployed"
_MAX_COMMITS = 5000
_MAX_SUBSCRIPTIONS = 5
_LISTEN_DAYS = 7
_ROLLOUT_SKILL = ".agents/skills/rollout-check/SKILL.md"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_CONFIG_KEYS = (
    "source",
    "workspace",
    "environment",
    "github_login",
    "github_user_id",
    "user_email",
    "slack_thread",
    "linear_issue",
    "github_issue",
    "agent_model_id",
    "agent_effort",
    "model_selection",
    "admin_thread",
    "draft_prs",
)


class RolloutEvent(BaseModel):
    """A deploy notice reduced to the target and the commits subscriptions match."""

    target: str
    commits: list[str]

    @classmethod
    def parse(cls, payload: object) -> RolloutEvent | None:
        """Return the event, or None when the body is not a rollout event."""
        if not isinstance(payload, dict):
            return None
        target = payload.get("target")
        commits = payload.get("commits")
        if not isinstance(target, str) or not _TARGET_RE.fullmatch(target.strip().lower()):
            return None
        if not isinstance(commits, list):
            return None
        kept: list[str] = []
        for commit in commits:
            if len(kept) >= _MAX_COMMITS:
                break
            if isinstance(commit, str) and _SHA_RE.fullmatch(commit.strip()):
                sha = commit.strip().lower()
                if sha not in kept:
                    kept.append(sha)
        if not kept:
            return None
        return cls(target=target.strip().lower(), commits=kept)


def _bearer(header: str) -> str:
    scheme, _, token = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _stored_body(target: str, commits: list[str]) -> bytes:
    """The payload subscriptions match: lowercase full SHAs, capped and deduped."""
    return json.dumps(
        {"target": target, "commits": commits},
        separators=(",", ":"),
    ).encode()


def _delivery_id(target: str, commits: list[str]) -> str:
    canonical = json.dumps({"commits": commits, "target": target}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ask_to_monitor(sha: str) -> str:
    """Tell the run to offer monitoring only when the repo skill says this change qualifies."""
    return prompt("runs/rollout-ask-to-monitor", merge_sha=remembered_merge_sha(sha))


_PR_URL_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def remembered_merge_sha(sha: str) -> str:
    """A full merge SHA safe to store, or empty when the value is not one."""
    cleaned = sha.strip().lower()
    return cleaned if _SHA_RE.fullmatch(cleaned) else ""


def _sha_for_pull_request(metadata: dict[str, object], url: str) -> str:
    """The merge SHA stored for ``url``, never one stored for a different pull request."""
    records = metadata.get("pull_requests")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and record.get("url") == url:
                sha = record.get(MERGE_COMMIT_SHA)
                return remembered_merge_sha(sha) if isinstance(sha, str) else ""
    if metadata.get(MERGE_COMMIT_PR_URL) == url:
        sha = metadata.get(MERGE_COMMIT_SHA)
        if isinstance(sha, str):
            return remembered_merge_sha(sha)
    return ""


def merged_pull_request(metadata: dict[str, object]) -> tuple[str, str, int, str] | None:
    """The thread's current pull request and its merge commit, once that commit is known."""
    url = metadata.get("pr_url")
    if not isinstance(url, str):
        return None
    sha = _sha_for_pull_request(metadata, url)
    match = _PR_URL_RE.fullmatch(url)
    if match is None or not sha:
        return None
    return match.group(1), match.group(2), int(match.group(3)), sha


def _requested_slack_thread(metadata: dict[str, object]) -> SlackThreadRef | None:
    """The Slack thread the rollout check was requested from, which keeps its results."""
    thread = SourceContext.parse(metadata.get(ROLLOUT_CHECK_CONTEXT)).slack_thread
    return thread if thread and thread.location else None


def _instructions(sha: str, metadata: dict[str, object]) -> str:
    return prompt(
        "runs/rollout-deployed",
        merge_sha=sha,
        slack_thread=_requested_slack_thread(metadata),
    )


def _copy_config(config: dict[str, JsonValue], values: dict[str, object]) -> None:
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool, dict, list)):
            config[key] = value


def _run_config(
    thread_id: str,
    owner: str,
    repo: str,
    number: int,
    workspace: str,
    metadata: dict[str, object],
) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = {}
    _copy_config(config, {key: metadata.get(key) for key in _CONFIG_KEYS})
    _copy_config(config, SourceContext.from_metadata(metadata).dump())
    email = metadata.get("triggering_user_email")
    if not isinstance(config.get("user_email"), str) and isinstance(email, str) and email:
        config["user_email"] = email
    if slack_thread := _requested_slack_thread(metadata):
        config["slack_thread"] = slack_thread.dump()
    config["thread_id"] = thread_id
    config["source"] = config.get("source") if isinstance(config.get("source"), str) else "github"
    config["repo"] = {"owner": owner, "name": repo}
    config["pr_number"] = number
    config["workspace"] = workspace
    return config


def _already_listening(subscriptions: list[EventSubscription], sha: str) -> bool:
    for subscription in subscriptions:
        match = subscription.payload_match
        commits = match.get("commits") if isinstance(match, dict) else None
        if (
            "deployment" in subscription.sources
            and _DEPLOYED in subscription.event_types
            and isinstance(commits, list)
            and sha in commits
        ):
            return True
    return False


async def _authorize(header: str) -> GitHubActionsClaims:
    """A verified workflow whose repository may already start threads."""
    token = _bearer(header)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        claims = await verify_github_oidc(token, _AUDIENCE)
    except InvalidFederatedToken as exc:
        logger.warning("Rejected rollout OIDC token", extra={"rollout_error": str(exc)})
        raise HTTPException(status_code=401, detail="Invalid token") from None
    if await WORKSPACES.thread_starter_of_repo(claims.repository) is None:
        logger.warning(
            "Rejected rollout event from a repository that may not start threads",
            extra={
                "rollout_repository": claims.repository,
                "rollout_workflow": claims.workflow_ref,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid token")
    return claims


async def accept_rollout_deploy(target: str, commits: list[str]) -> RolloutAccepted:
    """Acknowledge a verified deploy that was written to the event log."""
    logger.info(
        "Accepted rollout deploy",
        extra={"rollout_target": target, "rollout_commits": len(commits)},
    )
    return {"status": "accepted", "target": target, "commits": len(commits)}


async def _deployment_workspace(owner: str, repo: str) -> tuple[Repository, str, UUID] | None:
    """The repository and workspace that can keep a deployment subscription, if any."""
    if not configured():
        return None
    repository = await Repository.get(f"{owner}/{repo}")
    workspace = await workspace_for_repo(owner, repo)
    if repository is None or not workspace:
        logger.info(
            "Skipping rollout monitoring; repository is not in a workspace",
            extra={"repository": f"{owner}/{repo}"},
        )
        return None
    workspace_id = await WORKSPACES.id_for_slug(workspace)
    if workspace_id is None:
        logger.info(
            "Skipping rollout monitoring; workspace has no id",
            extra={"workspace": workspace},
        )
        return None
    return repository, workspace, workspace_id


async def subscribe_merged_thread(
    thread_id: str,
    *,
    owner: str,
    repo: str,
    number: int,
    sha: str,
    metadata: dict[str, object],
) -> None:
    """Listen for deploys that include this pull request's merge commit.

    Best-effort: a failure here leaves the merged thread state as it was.
    ``one_shot`` stays off so the first production region does not cancel the
    rest.
    """
    merge_sha = sha.strip().lower()
    if not _SHA_RE.fullmatch(merge_sha):
        return
    try:
        listener = await _deployment_workspace(owner, repo)
        if listener is None:
            return
        repository, workspace, workspace_id = listener
        existing = await EventSubscription.for_thread(thread_id)
        if _already_listening(existing, merge_sha):
            return
        if len(existing) >= _MAX_SUBSCRIPTIONS:
            logger.warning(
                "Skipping deploy subscription; thread is at the subscription limit",
                extra={"agent_thread_id": thread_id},
            )
            return
        await EventSubscription(
            thread_id=thread_id,
            workspace_id=workspace_id,
            sources=["deployment"],
            repository_id=repository.id,
            pull_request_id=None,
            event_types=[_DEPLOYED],
            payload_match={"commits": [merge_sha]},
            multitask_strategy="enqueue",
            one_shot=False,
            instructions=_instructions(merge_sha, metadata),
            run_config=_run_config(thread_id, owner, repo, number, workspace, metadata),
            expires_at=datetime.now(UTC) + timedelta(days=_LISTEN_DAYS),
        ).create()
    except Exception:  # noqa: BLE001
        logger.warning(
            "Subscribing a merged thread to deployments failed",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )


async def _rollout_skill_at_merge(owner: str, repo: str, sha: str) -> bool:
    """Whether this merge commit contains the repository rollout skill."""
    merge_sha = remembered_merge_sha(sha)
    if not merge_sha:
        logger.info(
            "Skipping rollout question; merge commit is unknown",
            extra={"repository": f"{owner}/{repo}"},
        )
        return False
    try:
        async with GitHubClient.as_app(owner, repo) as github:
            body = await github.repo(owner, repo).raw_file(_ROLLOUT_SKILL, merge_sha)
    except GitHubAppUnavailable, httpx2.HTTPError:
        logger.warning(
            "Skipping rollout question; rollout skill could not be read",
            extra={"repository": f"{owner}/{repo}"},
            exc_info=True,
        )
        return False
    if body is None:
        logger.info(
            "Skipping rollout question; repository has no rollout skill at the merge commit",
            extra={"repository": f"{owner}/{repo}"},
        )
        return False
    return True


async def ask_to_monitor_rollout(
    thread_id: str,
    *,
    owner: str,
    repo: str,
    number: int,
    metadata: dict[str, object],
    sha: str = "",
) -> None:
    """Start a run that asks only when the repository rollout skill says to."""
    try:
        if await _deployment_workspace(owner, repo) is None:
            return
        if not await _rollout_skill_at_merge(owner, repo, sha):
            return
        workspace = metadata.get("workspace")
        config = _run_config(
            thread_id,
            owner,
            repo,
            number,
            workspace if isinstance(workspace, str) else "",
            metadata,
        )
        source = config.get("source")
        await dispatch_agent_run(
            thread_id,
            _ask_to_monitor(sha),
            config,
            source=source if isinstance(source, str) else "github",
            thread_title=None,
            multitask_strategy="enqueue",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Asking whether to monitor a rollout failed",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )


@router.post("/webhooks/rollout")
async def rollout_webhook(request: Request) -> RolloutAccepted:
    """Verify a deployment event, record it, and acknowledge it."""
    claims = await _authorize(request.headers.get("Authorization", ""))
    body = await request.body()
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    event = RolloutEvent.parse(payload)
    if event is None:
        raise HTTPException(status_code=400, detail="Invalid rollout event")
    stored = await EventLog.record(
        request,
        _stored_body(event.target, event.commits),
        "deployment",
        event_type=_DEPLOYED,
        delivery_id=_delivery_id(event.target, event.commits),
        refs=EventRefs(github_repository=claims.repository),
    )
    if configured() and not stored:
        raise HTTPException(status_code=503, detail="Deployment event was not recorded")
    return await accept_rollout_deploy(event.target, event.commits)
