"""Accept a deployment event after an environment has finished syncing.

The route trusts a GitHub Actions OIDC token instead of a shared secret. A
verified deploy is written to the event log so a thread that subscribed when
its pull request merged can wake through ``listen_events``.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, JsonValue

from openswe.database import configured
from openswe.federation.github_oidc import GitHubActionsClaims, InvalidFederatedToken
from openswe.federation.github_oidc import verify as verify_github_oidc
from openswe.github.repositories import Repository
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
_DEPLOYED = "deployed"
_MAX_COMMITS = 5000
_MAX_SUBSCRIPTIONS = 5
_LISTEN_DAYS = 7
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_MERGE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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


def _instructions(sha: str) -> str:
    return (
        f"This deployment includes merge commit {sha}. "
        "The event names the target that finished syncing. "
        "Confirm the fix and look for regressions. "
        "Cancel this subscription with listen_events after every production target "
        "expected for this change has included the commit."
    )


def _run_config(
    thread_id: str,
    owner: str,
    repo: str,
    number: int,
    workspace: str,
    metadata: dict[str, object],
) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = {}
    for key in _CONFIG_KEYS:
        value = metadata.get(key)
        if isinstance(value, (str, int, float, bool, dict, list)):
            config[key] = value
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
    if not _MERGE_SHA_RE.fullmatch(merge_sha) or not configured():
        return
    try:
        repository = await Repository.get(f"{owner}/{repo}")
        workspace = await workspace_for_repo(owner, repo)
        if repository is None or not workspace:
            logger.info(
                "Skipping deploy subscription; repository is not in a workspace",
                extra={"agent_thread_id": thread_id, "repository": f"{owner}/{repo}"},
            )
            return
        workspace_id = await WORKSPACES.id_for_slug(workspace)
        if workspace_id is None:
            logger.info(
                "Skipping deploy subscription; workspace has no id",
                extra={"agent_thread_id": thread_id, "workspace": workspace},
            )
            return
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
            instructions=_instructions(merge_sha),
            run_config=_run_config(thread_id, owner, repo, number, workspace, metadata),
            expires_at=datetime.now(UTC) + timedelta(days=_LISTEN_DAYS),
        ).create()
    except Exception:  # noqa: BLE001
        logger.warning(
            "Subscribing a merged thread to deployments failed",
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
