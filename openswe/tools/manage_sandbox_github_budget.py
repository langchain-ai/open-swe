"""Read the sandbox GitHub API budget privately or change it through authorized admin writes."""

from typing import Literal

from pydantic import BaseModel, Field

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.settings import (
    SandboxGitHubBudgetMode,
    SettingsUpdate,
    get_instance_settings,
    instance_record,
    upsert_instance_settings,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack())
_FIELDS = {
    "mode": "sandbox_github_budget_mode",
    "per_hour": "sandbox_github_budget_per_hour",
    "brake_percent": "sandbox_github_brake_percent",
}


class SandboxGitHubBudgetSettings(BaseModel):
    """The budget fields to change; a field passed as null restores its default."""

    mode: SandboxGitHubBudgetMode | None = None
    per_hour: int | None = Field(default=None, ge=1, strict=True)
    brake_percent: int | None = Field(default=None, ge=0, le=100, strict=True)

    def changes(self) -> dict[str, object]:
        return {
            field: getattr(self, name)
            for name, field in _FIELDS.items()
            if name in self.model_fields_set
        }


@audit_tool(reads={"read"})
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_sandbox_github_budget(
    action: Literal["read", "set"], settings: SandboxGitHubBudgetSettings | None = None
) -> dict[str, object]:
    """Read or set how sandboxes' GitHub API calls on the App installation's token are metered."""
    if action == "set":
        if settings is None or not settings.changes():
            raise ValueError("Provide at least one budget setting to set")
        await upsert_instance_settings(
            SettingsUpdate.model_validate({**await instance_record(), **settings.changes()})
        )
    elif settings is not None:
        raise ValueError("Omit settings when reading")
    effective = await get_instance_settings()
    return {
        "scope": "instance",
        "effective": {name: effective.get(field) for name, field in _FIELDS.items()},
    }
