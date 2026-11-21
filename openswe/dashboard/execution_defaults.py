"""Robin-wide instructions and sandbox configuration under Defaults."""

import logging

from fastapi import APIRouter
from pydantic import model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.middleware import audit_endpoint
from openswe.audit_logs.models import SettingsChange
from openswe.dashboard.deps import ADMIN_DEP
from openswe.dashboard.settings_records import SettingsRecords
from openswe.sandboxes.configuration import ExecutionConfiguration
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class ExecutionDefaults(ExecutionConfiguration):
    @model_validator(mode="before")
    @classmethod
    def historical_enabled(cls, value: object) -> object:
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "enabled"}
        return value


class ExecutionDefaultsStore:
    def __init__(self) -> None:
        self.records = SettingsRecords("execution")

    async def load(self, *, session: AsyncSession | None = None) -> ExecutionDefaults:
        return ExecutionDefaults.stored(await self.records.get(session=session))

    async def update(
        self, update: ExecutionDefaults, *, expected_version: str | None = None
    ) -> ExecutionDefaults:
        changes: JsonObject = update.model_dump(mode="json", exclude_unset=True)
        if expected_version is not None:
            snapshot = await self.records.snapshot()
            if snapshot.version != expected_version:
                raise ValueError(
                    "Settings changed; read the resource privately again before retrying"
                )
            before, saved = snapshot.value, {**snapshot.value, **changes}
            try:
                ExecutionDefaults.model_validate(saved)
            except ValueError:
                raise ValueError(
                    "Invalid execution settings; inspect the resource privately"
                ) from None
            await self.records.put("", saved, expected_version=expected_version)
        elif changes:
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
        settings = ExecutionDefaults.stored(saved)
        await Topic.SETTINGS.invalidate()
        from openswe.execution_images import EXECUTION_IMAGES

        try:
            await EXECUTION_IMAGES.ensure_refresh_cron()
        except Exception:
            logger.warning("Could not schedule the Robin-wide image refresh", exc_info=True)
        return settings


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
