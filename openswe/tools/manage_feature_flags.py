"""Read shared feature flags privately or change them through authorized admin writes."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.feature_flags import feature_flag_names
from openswe.dashboard.settings import (
    SettingsUpdate,
    get_instance_settings,
    instance_record,
    upsert_instance_settings,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

FEATURE_FLAGS = feature_flag_names(SettingsUpdate)
_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope"))


@audit_tool(reads={"read"})
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_feature_flags(
    action: Literal["read", "set"],
    flags: dict[str, bool | None] | None = None,
) -> dict[str, object]:
    """Read or set Robin-wide feature flags without replacing other settings."""
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

    if flags:
        await upsert_instance_settings(
            SettingsUpdate.model_validate({**await instance_record(), **flags})
        )
    effective = await get_instance_settings()
    return {
        "scope": "instance",
        "effective": {key: effective.get(key) for key in sorted(FEATURE_FLAGS)},
    }
