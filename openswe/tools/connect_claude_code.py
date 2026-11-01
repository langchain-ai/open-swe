"""Start connecting a person's Claude Code to Open SWE without the token passing through the agent."""

from uuid import uuid7

from pydantic import ValidationError

from openswe.audit_logs.tools import audit_tool
from openswe.claude_code.routes import CONNECTIONS_PATH, SyncTokenCreate
from openswe.dashboard.oauth import CONNECT_CLAIM_TTL_SECONDS, ConnectClaim
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_api_base_url


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner"))
async def connect_claude_code(machine_name: str) -> dict[str, object]:
    """Implement the `connect_claude_code` tool."""
    login = configurable().github_login
    user = await User.for_login("github", login) if login else None
    if login is None or user is None:
        return {"ok": False, "error": "Could not resolve the caller's Open SWE user"}
    try:
        name = SyncTokenCreate(name=machine_name).name
    except ValidationError as exc:
        return {"ok": False, "error": exc.errors()[0]["msg"]}
    claim = ConnectClaim(sub=login, user_id=user.id, name=name, jti=uuid7())
    return {
        "ok": True,
        "machine_name": name,
        "claim_url": dashboard_api_base_url() + CONNECTIONS_PATH,
        "claim": claim.issue(),
        "expires_in_seconds": CONNECT_CLAIM_TTL_SECONDS,
    }
