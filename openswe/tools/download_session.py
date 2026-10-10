"""Prepare an Open SWE thread to continue as a local coding session, and the URL its transcript is fetched from."""

from openswe.audit_logs.tools import audit_tool
from openswe.threads.session_download import prepare_session_download
from openswe.tools.access import Policy, access, unchanged
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=unchanged))
async def download_session(thread_id: str, cwd: str) -> dict[str, object]:
    """Implement the `download_session` tool."""
    cfg = configurable()
    if not cfg.github_login:
        return {"ok": False, "error": "Could not resolve the caller's GitHub login"}
    download = await prepare_session_download(
        thread_id, cwd, cfg.github_login, email=cfg.user_email
    )
    return {"ok": True, **download.model_dump(), "next_step": download.next_step}
