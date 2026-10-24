"""Connect the caller's machine to Open SWE, so its Claude Code sessions mirror as read-only threads."""

from openswe.audit_logs.tools import audit_tool
from openswe.claude_code.connect import Connection
from openswe.claude_code.sync_tokens import NAME_MAX_CHARS
from openswe.database import postgres
from openswe.prompts import prompt
from openswe.tools.access import Policy, access, unchanged
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User


@expose_mcp()
@audit_tool()
@access(Policy(trusted="private", actor="owner", sole=unchanged))
async def connect_claude_code(machine_name: str) -> dict[str, object]:
    """Implement the `connect_claude_code` tool."""
    cfg = configurable()
    if not cfg.github_login:
        return {"ok": False, "error": "Could not resolve the caller's GitHub login"}
    name = machine_name.strip()[:NAME_MAX_CHARS]
    if not name:
        return {"ok": False, "error": "machine_name must not be blank"}
    if not postgres.configured():
        return {"ok": False, "error": "Claude Code sync requires PostgreSQL on this deployment"}
    user = await User.for_login("github", cfg.github_login)
    if user is None:
        return {"ok": False, "error": "Sign in to the Open SWE dashboard once, then try again"}
    connection = await Connection.open(user_id=user.id, machine_name=name)
    return {
        "ok": True,
        "machine_name": connection.token.name,
        "token_suffix": connection.token.token_suffix,
        "settings": connection.settings,
        "next_step": prompt("tools/connect-claude-code-next-step"),
    }
