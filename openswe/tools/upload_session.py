"""Reserve the Robin thread a local coding session continues or is imported into, and the URL its transcript is posted to."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.prompts import prompt
from openswe.threads.session_upload import (
    SessionType,
    SessionUploadHeader,
    reserve_session_upload,
)
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
@audit_tool()
# Its upload_token is a credential, so it is never offered where others read the thread.
@access(Policy(trusted="private", actor="owner"))
async def upload_session(
    type: SessionType = "claude",
    repo: str | None = None,
    branch: str | None = None,
    pr_url: str | None = None,
    visibility: Literal["workspace", "private"] | None = None,
) -> dict[str, object]:
    """Implement the `upload_session` tool."""
    cfg = configurable()
    if not cfg.github_login:
        return {"ok": False, "error": "Could not resolve the caller's GitHub login"}
    header = SessionUploadHeader(
        type=type, repo=repo, branch=branch, pr_url=pr_url, visibility=visibility
    )
    reservation = await reserve_session_upload(header, cfg.github_login, email=cfg.user_email)
    return {
        "ok": True,
        **reservation.model_dump(),
        "next_step": prompt(
            "tools/upload-session-next-step",
            upload_url=reservation.upload_url,
            upload_token=reservation.upload_token,
            batch=reservation.thread_id is None,
        ),
    }
