"""Read the approval policy from a PR's base commit."""

import logging

import httpx2
from pydantic import BaseModel

from agent.github.thread_token import get_github_token
from agent.review.approval import PolicySnapshot
from agent.review.approval_github import fetch_approval_policy
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


class ApprovalPolicyResult(BaseModel):
    policy: PolicySnapshot | None = None
    error: str | None = None


async def get_review_approval_policy() -> dict[str, object]:
    """Read the current base-branch approval policy for this review."""
    cfg = RunConfig.from_runtime()
    token = get_github_token()
    if cfg.is_eval:
        return ApprovalPolicyResult(
            error="Approval policy evaluation is disabled in benchmark mode."
        ).model_dump()
    if cfg.repo is None or cfg.pr_number is None or not token:
        return ApprovalPolicyResult(
            error="Review repository, PR, or GitHub authentication unavailable."
        ).model_dump()
    try:
        policy = await fetch_approval_policy(cfg.repo.owner, cfg.repo.name, cfg.pr_number, token)
    except httpx2.HTTPError, ValueError:
        logger.warning(
            "Could not load review approval policy",
            extra={"pr_number": cfg.pr_number},
            exc_info=True,
        )
        return ApprovalPolicyResult(
            error="Approval policy could not be read or is invalid. Treat eligibility as unknown."
        ).model_dump()
    return ApprovalPolicyResult(policy=policy).model_dump()
