"""Accept a deployment event after an environment has finished syncing.

The route trusts a GitHub Actions OIDC token instead of a shared secret. A
verified deploy is written to the event log so a thread can listen for it.
"""

import hashlib
import json
import logging
import re
from typing import Literal, TypedDict, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import JsonValue

from openswe.config import ENV
from openswe.database import configured
from openswe.federation.github_oidc import GitHubActionsClaims, InvalidFederatedToken
from openswe.federation.github_oidc import verify as verify_github_oidc
from openswe.webhooks.event_log import EventLog, EventRefs

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


def parse_rollout_event(payload: object) -> tuple[str, list[str]] | None:
    """Return ``(target, commits)`` or None when the body is not a rollout event."""
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
    return target.strip().lower(), kept


def _bearer(header: str) -> str:
    scheme, _, token = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _workflow_allowed(workflow_ref: str, allowed: list[str]) -> bool:
    path = workflow_ref.split("@", 1)[0]
    return any(path == workflow.strip().lstrip("/") for workflow in allowed)


def _owner(repository: str) -> str:
    return repository.split("/", 1)[0].strip().lower()


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
    orgs = {org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()}
    if not orgs:
        logger.warning("ALLOWED_GITHUB_ORGS is not configured — rejecting rollout event")
        raise HTTPException(status_code=401, detail="Invalid token")
    token = _bearer(header)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        claims = await verify_github_oidc(token, _AUDIENCE)
    except InvalidFederatedToken as exc:
        logger.warning("Rejected rollout OIDC token", extra={"rollout_error": str(exc)})
        raise HTTPException(status_code=401, detail="Invalid token") from None
    if _owner(claims.repository) not in orgs:
        logger.warning(
            "Rejected rollout event from an unlisted repository",
            extra={"rollout_repository": claims.repository},
        )
        raise HTTPException(status_code=401, detail="Invalid token")
    workflows = ENV.ROLLOUT_OIDC_WORKFLOWS.get_list()
    if not workflows:
        logger.warning("ROLLOUT_OIDC_WORKFLOWS is not configured — rejecting rollout event")
        raise HTTPException(status_code=401, detail="Invalid token")
    if not _workflow_allowed(claims.workflow_ref, workflows):
        logger.warning(
            "Rejected rollout event from an unlisted workflow",
            extra={"rollout_workflow": claims.workflow_ref},
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
    parsed = parse_rollout_event(payload)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid rollout event")
    target, commits = parsed
    stored_payload: dict[str, JsonValue] = {
        "target": target,
        "commits": cast(list[JsonValue], commits),
    }
    stored = await EventLog.record(
        request,
        _stored_body(target, commits),
        "deployment",
        event_type=_DEPLOYED,
        delivery_id=_delivery_id(target, commits),
        refs=EventRefs(github_repository=claims.repository),
        payload=stored_payload,
    )
    if configured() and not stored:
        raise HTTPException(status_code=503, detail="Deployment event was not recorded")
    return await accept_rollout_deploy(target, commits)
