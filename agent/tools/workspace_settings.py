"""Admin-thread tools for the workspace settings visibility toggles.

Wired into admin threads; each tool rechecks user or system authorization.
"""

from typing import TypedDict

from agent.dashboard import workspace_settings
from agent.dashboard.workspace_settings import (
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
    WorkspaceSettingsView,
)
from agent.tools.admin_gate import require_admin

_ACTION = "manage workspace settings"


class ModelIdentityVisibilityResult(TypedDict, total=False):
    ok: bool
    error: str
    workspace: str | None
    settings: WorkspaceSettingsView


async def set_model_identity_visibility(
    show_model_identity: bool | None,
    workspace: str | None = None,
) -> ModelIdentityVisibilityResult:
    """Set the model-identity toggle; ``None`` inherits the tier below, no ``workspace`` writes the instance."""
    if error := await require_admin(_ACTION):
        return {"ok": False, "error": error}
    try:
        if workspace and workspace.strip():
            slug = workspace_settings.resolve_settings_workspace(workspace)
            current = await workspace_settings.workspace_settings_view(slug)
            view = await workspace_settings.upsert_workspace_overrides(
                slug,
                WorkspaceSettingsUpdate(
                    **{
                        **current["overrides"],
                        "show_model_identity": show_model_identity,
                    }
                ),
            )
            return {"ok": True, "workspace": slug, "settings": view}
        current = await workspace_settings.get_instance_settings()
        await workspace_settings.upsert_instance_settings(
            WorkspaceSettingsUpdate(
                **{
                    **dict(current),
                    "show_model_identity": show_model_identity,
                }
            )
        )
        effective: WorkspaceSettings = await workspace_settings.get_instance_settings()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "workspace": None,
        "settings": {"effective": dict(effective), "overrides": {}},
    }
