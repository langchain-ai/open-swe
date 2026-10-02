"""Accept a deployment event after an environment has finished syncing.

The route verifies the signature and acknowledges the delivery. Matching the
event to a pull request this server worked on arrives with the rollout watch.
"""

import hashlib
import hmac
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from openswe.config import ENV

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_COMMITS = 5000
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def verify_rollout_signature(body: bytes, signature: str, *, secret: str) -> bool:
    """True when ``signature`` is the HMAC-SHA256 of ``body`` as ``sha256=<hex>``."""
    if not secret:
        logger.warning("ROLLOUT_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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


async def accept_rollout_deploy(target: str, commits: list[str]) -> dict[str, Any]:
    """Acknowledge a verified deploy. Watch matching is added with rollouts."""
    logger.info(
        "Accepted rollout deploy",
        extra={"rollout_target": target, "rollout_commits": len(commits)},
    )
    return {"status": "accepted", "target": target, "commits": len(commits)}


@router.post("/webhooks/rollout")
async def rollout_webhook(request: Request) -> dict[str, Any]:
    """Verify a deployment event and acknowledge it."""
    body = await request.body()
    signature = request.headers.get("X-Rollout-Signature-256", "")
    if not verify_rollout_signature(body, signature, secret=ENV.ROLLOUT_WEBHOOK_SECRET.get()):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    parsed = parse_rollout_event(payload)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid rollout event")
    target, commits = parsed
    return await accept_rollout_deploy(target, commits)
