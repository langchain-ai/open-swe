"""Robin-wide instructions and sandbox configuration, stored independently of environments."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.middleware import audit_endpoint
from openswe.audit_logs.models import SettingsChange
from openswe.dashboard.deps import ADMIN_DEP
from openswe.dashboard.settings_records import SettingsRecords
from openswe.utils.json_types import JsonObject
from openswe.workspaces.store import WorkspaceUpdate


class ExecutionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    repos: list[str] = Field(default_factory=list)
    setup_script: str = ""
    update_script: str = ""
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    base_snapshot_id: str | None = None
    vcpus: int | None = Field(default=None, gt=0)
    mem_bytes: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = WorkspaceUpdate.model_validate(value).model_dump(exclude_unset=True)
        return {**value, **normalized}

    @field_validator("snapshot_id", "base_snapshot_id")
    @classmethod
    def normalize_snapshot(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class ExecutionDefaultsStore:
    def __init__(self) -> None:
        self.records = SettingsRecords("execution")

    async def load(self) -> ExecutionDefaults:
        return ExecutionDefaults.model_validate(await self.records.get() or {})

    async def update(self, update: ExecutionDefaults) -> ExecutionDefaults:
        changes: JsonObject = update.model_dump(mode="json", exclude_unset=True)
        if changes:
            before, saved = await self.records.patch("", changes)
        else:
            before = saved = await self.records.get() or {}
        if entry := current_audit_log.get():
            entry.enrichments.settings_scope = "execution"
            entry.enrichments.settings_changes = {
                field: SettingsChange(before="[REDACTED]", after="[REDACTED]")
                for field, value in changes.items()
                if before.get(field) != value
            }
        return ExecutionDefaults.model_validate(saved)


EXECUTION_DEFAULTS = ExecutionDefaultsStore()
router = APIRouter(tags=["settings"])


@router.get("/settings/execution")
async def api_get_execution_defaults(_admin: dict[str, object] = ADMIN_DEP) -> ExecutionDefaults:
    return await EXECUTION_DEFAULTS.load()


@router.patch("/settings/execution")
@audit_endpoint
async def api_patch_execution_defaults(
    body: ExecutionDefaults, _admin: dict[str, object] = ADMIN_DEP
) -> ExecutionDefaults:
    return await EXECUTION_DEFAULTS.update(body)
