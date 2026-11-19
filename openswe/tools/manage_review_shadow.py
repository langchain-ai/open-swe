"""Read the shadow review experiment privately or change it through authorized admin writes."""

from typing import Literal

from pydantic import BaseModel, Field

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.settings import (
    SettingsUpdate,
    get_instance_settings,
    instance_record,
    upsert_instance_settings,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope"))
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


@audit_tool(reads={"read"})
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_review_shadow(
    action: Literal["read", "set"],
    settings: ReviewShadowSettings | None = None,
) -> dict[str, object]:
    """Read or set which share of pull requests also get a shadow review, and on which model."""
    if action == "set":
        if settings is None or not settings.changes():
            raise ValueError("Provide at least one shadow review setting to set")
    elif settings is not None:
        raise ValueError("Omit settings when reading")
    changes = settings.changes() if settings is not None else {}

    if changes:
        await upsert_instance_settings(
            SettingsUpdate.model_validate({**await instance_record(), **changes})
        )
    effective = await get_instance_settings()
    return {"scope": "instance", "effective": {key: effective.get(key) for key in _FIELDS}}
