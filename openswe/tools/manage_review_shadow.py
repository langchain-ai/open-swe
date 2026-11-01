"""Read the shadow review experiment privately or change it through authorized admin writes."""

from typing import Literal

from pydantic import BaseModel, Field

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_instance_settings,
    instance_record,
    upsert_instance_settings,
    upsert_workspace_overrides,
    workspace_record,
    workspace_settings_view,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces.store import WORKSPACES, slugify

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope", "workspace"))
_FIELDS = ("review_shadow_percent", "review_shadow_model", "review_shadow_reasoning_effort")


class ReviewShadowSettings(BaseModel):
    """The shadow review fields to change; a field passed as null inherits again."""

    percent: int | None = Field(default=None, ge=0, le=100, strict=True)
    model: str | None = None
    reasoning_effort: str | None = None

    def changes(self) -> dict[str, object]:
        return {
            f"review_shadow_{name}": getattr(self, name)
            for name in ("percent", "model", "reasoning_effort")
            if name in self.model_fields_set
        }


@audit_tool(reads={"read"}, target_workspace="workspace")
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_review_shadow(
    action: Literal["read", "set"],
    settings: ReviewShadowSettings | None = None,
    workspace: str | None = None,
) -> dict[str, object]:
    """Read or set which share of pull requests also get a shadow review, and on which model."""
    if action == "set":
        if settings is None or not settings.changes():
            raise ValueError("Provide at least one shadow review setting to set")
    elif settings is not None:
        raise ValueError("Omit settings when reading")
    changes = settings.changes() if settings is not None else {}

    if workspace is not None:
        slug = slugify(workspace)
        if await WORKSPACES.get(slug) is None:
            raise ValueError("Workspace not found")
        if changes:
            view = await upsert_workspace_overrides(
                slug,
                WorkspaceSettingsUpdate.model_validate({**await workspace_record(slug), **changes}),
            )
        else:
            view = await workspace_settings_view(slug)
        return {
            "workspace": slug,
            "effective": {key: view["effective"].get(key) for key in _FIELDS},
            "overrides": {
                key: view["overrides"][key] for key in _FIELDS if key in view["overrides"]
            },
        }

    if changes:
        await upsert_instance_settings(
            WorkspaceSettingsUpdate.model_validate({**await instance_record(), **changes})
        )
    effective = await get_instance_settings()
    return {"scope": "instance", "effective": {key: effective.get(key) for key in _FIELDS}}
