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
    slack_bot_trigger_mode: Literal["mentions", "true", "false"] | None = None,
) -> dict[str, object]:
    """Manage global flags and allowlisted Slack bots: mentions (default), true (listened channels too), or false (disabled)."""
    if action == "set":
        if not flags and slack_bot_trigger_mode is None:
            raise ValueError("Provide at least one feature flag to set")
        unknown = (flags or {}).keys() - FEATURE_FLAGS
        if unknown:
            raise ValueError(f"Unsupported feature flags: {', '.join(sorted(unknown))}")
        if any(
            value is not None and not isinstance(value, bool) for value in (flags or {}).values()
        ):
            raise ValueError("Feature flags must be true, false, or null to inherit")
        if slack_bot_trigger_mode not in {None, "mentions", "true", "false"}:
            raise ValueError("Bot trigger mode must be mentions, true, or false")
    elif flags is not None or slack_bot_trigger_mode is not None:
        raise ValueError("Omit flags when reading")

    if flags or slack_bot_trigger_mode is not None:
        updates: dict[str, object] = dict(flags or {})
        if slack_bot_trigger_mode is not None:
            updates["slack_bot_trigger_mode"] = slack_bot_trigger_mode
        await upsert_instance_settings(
            SettingsUpdate.model_validate({**await instance_record(), **updates})
        )
    effective = await get_instance_settings()
    return {
        "scope": "instance",
        "effective": {
            key: effective.get(key) for key in sorted(FEATURE_FLAGS | {"slack_bot_trigger_mode"})
        },
    }
