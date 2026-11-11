"""Read the apps reviews do work for, or add and remove them through authorized admin writes."""

from typing import Literal
from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.workspace_settings import (
    AppRelease,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
    get_instance_settings,
    instance_record,
    upsert_instance_settings,
    upsert_workspace_overrides,
    workspace_record,
    workspace_settings_view,
)
from openswe.plugins.records import PluginApps
from openswe.review_apps.apps import ReviewApp
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces.store import WORKSPACES, slugify

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope", "workspace", "apps"))


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_review_apps(
    action: Literal["read", "add", "remove"],
    app: str | None = None,
    workspace: str | None = None,
) -> dict[str, object]:
    """Read, add or remove the apps whose review step reviews follow."""
    if action == "read":
        if app is not None:
            raise ValueError("Omit app when reading")
    elif action in ("add", "remove"):
        if not app:
            raise ValueError("Provide the app's id")
    else:
        raise ValueError("Action must be read, add or remove")
    slug = slugify(workspace) if workspace is not None else None
    if slug is not None and await WORKSPACES.get(slug) is None:
        raise ValueError("Workspace not found")
    current = (
        WorkspaceSettings((await workspace_settings_view(slug))["effective"])
        if slug is not None
        else await get_instance_settings()
    ).review_app_releases
    if app:
        chosen = UUID(app.strip())
        if action == "add":
            # Pins the newest release, so the owner's later ones wait for an admin.
            release = await PluginApps.newest_release(chosen)
            if release is None or release.manifest.review is None:
                raise ValueError("That app has no released version with a review step")
            current = {**current, chosen: release.id}
        else:
            current = {found: pinned for found, pinned in current.items() if found != chosen}
        apps = [
            AppRelease(app=str(found), release=str(pinned)) for found, pinned in current.items()
        ]
        if slug is not None:
            await upsert_workspace_overrides(
                slug,
                WorkspaceSettingsUpdate.model_validate(
                    {**await workspace_record(slug), "review_apps": apps}
                ),
            )
        else:
            await upsert_instance_settings(
                WorkspaceSettingsUpdate.model_validate(
                    {**await instance_record(), "review_apps": apps}
                )
            )
    return {
        "scope": "workspace" if slug is not None else "instance",
        "workspace": slug,
        "apps": await ReviewApp.describe(current),
    }
