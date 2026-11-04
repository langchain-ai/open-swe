"""Read or set a workspace bot's LangSmith Managed Tools kit and profile through admin writes."""

from typing import Literal

from pydantic import BaseModel

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_workspace_overrides,
    workspace_record,
    workspace_settings_view,
)
from openswe.mcp.managed import BotTools, ManagedToolsError
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces.store import WORKSPACES, slugify

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("workspace", "ready"))


class WorkspaceBotSettings(BaseModel):
    """The bot fields to change; a field passed as null clears it."""

    kit_id: str | None = None
    profile_id: str | None = None

    def changes(self) -> dict[str, object]:
        fields = {"kit_id": "managed_tools_gateway_id", "profile_id": "managed_tools_profile_id"}
        return {fields[name]: getattr(self, name) for name in self.model_fields_set}


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_workspace_bot(
    action: Literal["read", "set"],
    workspace: str,
    settings: WorkspaceBotSettings | None = None,
) -> dict[str, object]:
    """Read or set the managed tools kit and LMT profile a workspace's bot runs."""
    slug = slugify(workspace)
    if await WORKSPACES.get(slug) is None:
        raise ValueError("Workspace not found")
    changes = settings.changes() if settings is not None else {}
    if action == "set":
        if not changes:
            raise ValueError("Provide kit_id, profile_id, or both")
        view = await upsert_workspace_overrides(
            slug,
            WorkspaceSettingsUpdate.model_validate({**await workspace_record(slug), **changes}),
        )
    elif changes:
        raise ValueError("Omit settings when reading")
    else:
        view = await workspace_settings_view(slug)
    result: dict[str, object] = {
        "workspace": slug,
        "kit_id": view["effective"].get("managed_tools_gateway_id"),
        "profile_id": view["effective"].get("managed_tools_profile_id"),
    }
    if (bot := await BotTools.for_workspace(slug)) is None:
        return {**result, "ready": False}
    try:
        status = await bot.status()
    except ManagedToolsError as exc:
        return {**result, "ready": False, "error": str(exc)}
    return {
        **result,
        "ready": not status.missing,
        "kit": status.kit.name,
        "services": [
            {"name": server.name, "connected": server.connected} for server in status.servers
        ],
    }
