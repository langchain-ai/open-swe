"""Accept a deployment event after an environment has finished syncing.

The route trusts a GitHub Actions OIDC token instead of a shared secret. Matching
the event to a pull request this server worked on arrives with the rollout watch.
"""

import json
import logging
import re
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, Request

from openswe.config import ENV
from openswe.federation.github_oidc import InvalidFederatedToken
from openswe.federation.github_oidc import verify as verify_github_oidc

logger = logging.getLogger(__name__)

router = APIRouter()


class RolloutAccepted(TypedDict):
    status: Literal["accepted"]
    target: str
    commits: int


_AUDIENCE = "openswe-rollout"
_MAX_COMMITS = 5000
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
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
    for workflow in allowed:
        needle = workflow.strip().lstrip("/")
        if needle and (path == needle or path.endswith("/" + needle)):
            return True
    return False


def _owner(repository: str) -> str:
    return repository.split("/", 1)[0].strip().lower()


async def _authorize(header: str) -> None:
    orgs = {org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()}
    if not orgs:
        logger.warning("ALLOWED_GITHUB_ORGS is not configured — rejecting rollout event")
        raise HTTPException(status_code=401, detail="Invalid token")
    token = _bearer(header)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        claims = await verify_github_oidc(token, expected_audience=_AUDIENCE)
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


async def accept_rollout_deploy(target: str, commits: list[str]) -> RolloutAccepted:
    """Acknowledge a verified deploy. Watch matching is added with rollouts."""
    logger.info(
        "Accepted rollout deploy",
        extra={"rollout_target": target, "rollout_commits": len(commits)},
    )
    return {"status": "accepted", "target": target, "commits": len(commits)}


@router.post("/webhooks/rollout")
async def rollout_webhook(request: Request) -> RolloutAccepted:
    """Verify a deployment event and acknowledge it."""
    await _authorize(request.headers.get("Authorization", ""))
    try:
        payload = json.loads(await request.body())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    parsed = parse_rollout_event(payload)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid rollout event")
    target, commits = parsed
    return await accept_rollout_deploy(target, commits)
