"""Read a workspace bot's LangSmith Managed Tools services, or register a new server for it."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import private_credential_login
from openswe.mcp.managed import BotTools, ManagedToolsError, NewServer, PersonalTools
from openswe.tools.access import Policy, access
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces.store import WORKSPACES, slugify

# Registering a server runs as the admin's own LangSmith sign-in, so it needs their
# private thread, like reading the bot's services.
_ADMIN = Policy(trusted="admin_surface", actor="admin")


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_ADMIN)
async def manage_workspace_bot(
    action: Literal["read", "add_server"],
    workspace: str,
    server: NewServer | None = None,
) -> dict[str, object]:
    """Read the managed tools services a workspace's bot can use, or register a new one."""
    slug = slugify(workspace)
    if await WORKSPACES.get(slug) is None:
        raise ValueError("Workspace not found")
    bot = await BotTools.of(slug)
    added: dict[str, object] = {}
    if action == "add_server":
        if server is None:
            raise ValueError("Provide the server to add")
        login = await private_credential_login()
        if login is None:
            raise ValueError("Adding a server needs your own LangSmith sign-in")
        created = await PersonalTools(login).create_server(server)
        added = {"added": {"id": created.id, "name": created.name, "kind": created.kind}}
    elif server is not None:
        raise ValueError("Omit server when reading")
    try:
        status = await bot.status()
    except ManagedToolsError as exc:
        return {"workspace": slug, **added, "error": str(exc)}
    return {
        "workspace": slug,
        **added,
        "services": [
            {"id": item.id, "name": item.name, "kind": item.kind, "connected": item.connected}
            for item in status.servers
        ],
    }
