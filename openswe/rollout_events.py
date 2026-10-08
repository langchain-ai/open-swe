"""Accept a deployment event after an environment has finished syncing.

The route trusts a GitHub Actions OIDC token instead of a shared secret. A
verified deploy is written to the event log so a thread can listen for it.
"""

import hashlib
import json
import logging
import re
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openswe.database import configured
from openswe.federation.github_oidc import GitHubActionsClaims, InvalidFederatedToken
from openswe.federation.github_oidc import verify as verify_github_oidc
from openswe.webhooks.event_log import EventLog, EventRefs
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
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


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
