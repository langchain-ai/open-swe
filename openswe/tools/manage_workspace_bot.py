"""Read or change a workspace bot's LangSmith Managed Tools kit and servers."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import private_credential_login
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_workspace_overrides,
    workspace_record,
)
from openswe.mcp.cards import connect_bot_service
from openswe.mcp.managed import BotTools, ManagedToolsError, NewServer, PersonalTools
from openswe.tools.access import Policy, access
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces.store import WORKSPACES, slugify

# Registering a server runs as the admin's own LangSmith sign-in, so every action needs
# their private thread.
_ADMIN = Policy(trusted="admin_surface", actor="admin")


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_ADMIN)
async def manage_workspace_bot(
    action: Literal["read", "add_server", "set_kit"],
    workspace: str,
    server: NewServer | None = None,
    kit_id: str | None = None,
) -> dict[str, object]:
    """Read a workspace bot's kit and servers, register a server for it, or point it at
    another LMT tool kit."""
    slug = slugify(workspace)
    if await WORKSPACES.get(slug) is None:
        raise ValueError("Workspace not found")
    if (server is not None) != (action == "add_server"):
        raise ValueError("Pass server only to add_server")
    if kit_id is not None and action != "set_kit":
        raise ValueError("Pass kit_id only to set_kit")
    result: dict[str, object] = {"workspace": slug}
    if action == "add_server" and server is not None:
        login = await private_credential_login()
        if login is None:
            raise ValueError("Adding a server needs your own LangSmith sign-in")
        created = await PersonalTools(login).create_server(server)
        result["added"] = {"id": created.id, "name": created.name, "kind": created.kind}
        if created.kind == "none":
            await connect_bot_service(login, await BotTools.of(slug), created.id)
    elif action == "set_kit":
        record = {**await workspace_record(slug), "managed_tools_gateway_id": kit_id}
        await upsert_workspace_overrides(slug, WorkspaceSettingsUpdate.model_validate(record))
    try:
        status = await (await BotTools.of(slug)).status()
    except ManagedToolsError as exc:
        return {**result, "error": str(exc)}
    return {
        **result,
        "kit": status.kit.name if status.kit else None,
        "servers": [
            {
                "id": item.id,
                "name": item.name,
                "kind": item.kind,
                "connected": item.connected,
                "tools": len(item.tools),
            }
            for item in status.servers
            if item.in_kit
        ],
        "missing": [item.name for item in status.missing],
    }
