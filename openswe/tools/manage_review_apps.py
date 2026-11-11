"""Read the apps reviews do work for, add and remove them, or set the checks that mean a
pull request's preview is deployed, through authorized admin writes."""

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
_WRITE = Policy(
    trusted="admin_surface",
    actor="admin",
    sole=ack("scope", "workspace", "apps", "preview_checks"),
)


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_review_apps(
    action: Literal["read", "add", "remove", "set_preview_checks"],
    app: str | None = None,
    workspace: str | None = None,
    preview_checks: list[str] | None = None,
) -> dict[str, object]:
    """Read, add or remove the apps whose review step reviews follow, or set the checks,
    by name, whose success means a pull request's preview serves its head commit: when one
    passes, reviews do their apps' work on that preview."""
    if action == "set_preview_checks":
        if app is not None or preview_checks is None:
            raise ValueError("Provide preview_checks, and omit app, to set them")
    elif preview_checks is not None:
        raise ValueError("Pass preview_checks only to set_preview_checks")
    elif action == "read":
        if app is not None:
            raise ValueError("Omit app when reading")
    elif action in ("add", "remove"):
        if not app:
            raise ValueError("Provide the app's id")
    else:
        raise ValueError("Action must be read, add, remove or set_preview_checks")
    slug = slugify(workspace) if workspace is not None else None
    if slug is not None and await WORKSPACES.get(slug) is None:
        raise ValueError("Workspace not found")
    settings = (
        WorkspaceSettings((await workspace_settings_view(slug))["effective"])
        if slug is not None
        else await get_instance_settings()
    )
    current = settings.review_app_releases
    changes: dict[str, object] = {}
    if preview_checks is not None:
        changes["review_app_preview_checks"] = preview_checks
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
        changes["review_apps"] = [
            AppRelease(app=str(found), release=str(pinned)) for found, pinned in current.items()
        ]
    checks = settings.review_app_preview_checks
    if changes:
        # Over the scope's own record, so its other settings stay as they are.
        if slug is not None:
            update = WorkspaceSettingsUpdate.model_validate(
                {**await workspace_record(slug), **changes}
            )
            await upsert_workspace_overrides(slug, update)
        else:
            update = WorkspaceSettingsUpdate.model_validate({**await instance_record(), **changes})
            await upsert_instance_settings(update)
        if preview_checks is not None:
            checks = frozenset(update.review_app_preview_checks or [])
    return {
        "scope": "workspace" if slug is not None else "instance",
        "workspace": slug,
        "apps": await ReviewApp.describe(current),
        "preview_checks": sorted(checks),
    }
