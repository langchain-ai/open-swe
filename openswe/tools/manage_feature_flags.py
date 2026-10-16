"""Read shared feature flags privately or change them through authorized admin writes."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.feature_flags import feature_flag_names
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

FEATURE_FLAGS = feature_flag_names(WorkspaceSettingsUpdate)
_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope", "workspace"))


@audit_tool(skip_read=True)
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_feature_flags(
    action: Literal["read", "set"],
    flags: dict[str, bool | None] | None = None,
    workspace: str | None = None,
) -> dict[str, object]:
    """Read or set instance or workspace feature flags without replacing other settings."""
    if action == "set":
        if not flags:
            raise ValueError("Provide at least one feature flag to set")
        unknown = flags.keys() - FEATURE_FLAGS
        if unknown:
            raise ValueError(f"Unsupported feature flags: {', '.join(sorted(unknown))}")
        if any(value is not None and not isinstance(value, bool) for value in flags.values()):
            raise ValueError("Feature flags must be true, false, or null to inherit")
    elif flags is not None:
        raise ValueError("Omit flags when reading")

    if workspace is not None:
        slug = slugify(workspace)
        if await WORKSPACES.get(slug) is None:
            raise ValueError("Workspace not found")
        if flags:
            view = await upsert_workspace_overrides(
                slug,
                WorkspaceSettingsUpdate.model_validate({**await workspace_record(slug), **flags}),
            )
        else:
            view = await workspace_settings_view(slug)
        return {
            "workspace": slug,
            "effective": {key: view["effective"].get(key) for key in sorted(FEATURE_FLAGS)},
            "overrides": {
                key: view["overrides"][key]
                for key in sorted(FEATURE_FLAGS)
                if key in view["overrides"]
            },
        }

    if flags:
        await upsert_instance_settings(
            WorkspaceSettingsUpdate.model_validate({**await instance_record(), **flags})
        )
    effective = await get_instance_settings()
    return {
        "scope": "instance",
        "effective": {key: effective.get(key) for key in sorted(FEATURE_FLAGS)},
    }
