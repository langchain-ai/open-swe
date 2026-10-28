"""Reserve the Open SWE thread a local coding session continues in, and the URL its transcript is posted to."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.prompts import prompt
from openswe.threads.session_upload import (
    SessionType,
    SessionUploadHeader,
    reserve_session_upload,
)
from openswe.tools.access import Policy, access, unchanged
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=unchanged))
async def upload_session(
    type: SessionType = "claude",
    repo: str | None = None,
    branch: str | None = None,
    pr_url: str | None = None,
    visibility: Literal["workspace", "private"] = "workspace",
) -> dict[str, object]:
    """Implement the `upload_session` tool."""
    cfg = configurable()
    if not cfg.github_login:
        return {"ok": False, "error": "Could not resolve the caller's GitHub login"}
    header = SessionUploadHeader(
        type=type, repo=repo, branch=branch, pr_url=pr_url, visibility=visibility
    )
    user = await User.for_login("github", cfg.github_login)
    if user is None:
        return {"ok": False, "error": "The caller has no Open SWE account"}
    reservation = await reserve_session_upload(
        header, cfg.github_login, user_id=user.id, email=cfg.user_email
    )
    return {
        "ok": True,
        **reservation.model_dump(),
        "next_step": prompt(
            "tools/upload-session-next-step",
            upload_url=reservation.upload_url,
            upload_token=reservation.upload_token,
        ),
    }
