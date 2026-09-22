"""Approve a pull request as the authenticated run requester."""

import logging
from typing import Annotated

from fastapi import HTTPException
from pydantic import Field

from agent.credential_scope import pr_author_login
from agent.dashboard.profiles import get_valid_access_token
from agent.github.pull_request_actions import ApproveAction, act_on_pull_request
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


async def approve_pull_request(
    owner: str,
    repo: str,
    number: int,
    sha: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{40,64}$")],
    confirm: bool,
) -> dict[str, object]:
    """Approve a PR at its current head only after the user explicitly requests it."""
    if not confirm:
        return {"success": False, "error": "Approval requires explicit confirmation."}
    cfg = RunConfig.from_runtime()
    if (
        cfg.source not in ("dashboard", "slack")
        or cfg.background_task_completion
        or cfg.schedule_id
        or cfg.watch_key
    ):
        return {"success": False, "error": "Approval requires a direct user request."}
    try:
        login = await pr_author_login()
    except Exception:
        logger.exception("Could not authorize pull request approval")
        return {"success": False, "error": "Could not verify the authenticated requester."}
    if not login:
        return {"success": False, "error": "Approval requires an authenticated user-owned thread."}
    token = await get_valid_access_token(login)
    if not token:
        return {"success": False, "error": "GitHub token unavailable, re-login required."}
    try:
        result = await act_on_pull_request(
            owner,
            repo,
            number,
            ApproveAction(action="approve", sha=sha),
            token,
        )
    except HTTPException as exc:
        return {
            "success": False,
            "error": str(exc.detail),
            "status_code": exc.status_code,
        }
    return {"success": result.done, "action": result.action}
