"""Instance-wide sandbox settings an admin can change at runtime.

New sandboxes boot from a base snapshot, normally configured with the
``DEFAULT_SANDBOX_SNAPSHOT_ID`` env var. Admins can override it from the
dashboard so rolling out a rebuilt base image does not require a redeploy: the
stored value wins, and an unset record falls back to the env var.

The value is an opaque provider-scoped identifier — for ``SANDBOX_TYPE=langsmith``
it is a LangSmith snapshot id — so it is stored as free text with no format
validation. An environment with a ready snapshot still takes precedence over this
base.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from agent.config import ENV
from agent.store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)

SANDBOX_SETTINGS_NAMESPACE: list[str] = ["sandbox_settings"]
SANDBOX_SETTINGS_KEY = "default"

BASE_SNAPSHOT_MAX_CHARS = 512

BaseSnapshotSource = Literal["admin", "env", "unset"]


class SandboxSettingsUpdate(BaseModel):
    base_snapshot_id: str | None = None

    @field_validator("base_snapshot_id", mode="before")
    @classmethod
    def _normalize_base_snapshot_id(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("base_snapshot_id must be a string")
        text = v.strip()
        if not text:
            return None
        if len(text) > BASE_SNAPSHOT_MAX_CHARS:
            raise ValueError(
                f"base_snapshot_id must be at most {BASE_SNAPSHOT_MAX_CHARS} characters"
            )
        return text


def env_base_snapshot_id() -> str | None:
    value = ENV.DEFAULT_SANDBOX_SNAPSHOT_ID.get().strip()
    return value or None


def _stored_base_snapshot_id(record: dict[str, Any] | None) -> str | None:
    snapshot_id = record.get("base_snapshot_id") if record else None
    return snapshot_id.strip() or None if isinstance(snapshot_id, str) else None


async def get_admin_base_snapshot_id() -> str | None:
    """Return the admin-configured base snapshot, ignoring the env default.

    Fail-soft on purpose: this runs while a sandbox is being created, and a
    store failure must fall back to ``DEFAULT_SANDBOX_SNAPSHOT_ID`` rather than
    fail the run.
    """
    try:
        record = await get_value(SANDBOX_SETTINGS_NAMESPACE, SANDBOX_SETTINGS_KEY)
    except Exception:
        logger.warning("sandbox settings lookup failed", exc_info=True)
        return None
    return _stored_base_snapshot_id(record)


async def resolve_base_snapshot_id() -> str | None:
    """Return the base snapshot new sandboxes boot from: admin setting, else env."""
    return await get_admin_base_snapshot_id() or env_base_snapshot_id()


async def get_sandbox_settings() -> dict[str, Any]:
    """Return the stored settings plus the resolved effective base snapshot."""
    value = await get_value(SANDBOX_SETTINGS_NAMESPACE, SANDBOX_SETTINGS_KEY) or {}
    admin_snapshot_id = _stored_base_snapshot_id(value)
    env_snapshot_id = env_base_snapshot_id()
    source: BaseSnapshotSource = (
        "admin" if admin_snapshot_id else ("env" if env_snapshot_id else "unset")
    )
    return {
        "base_snapshot_id": admin_snapshot_id,
        "env_base_snapshot_id": env_snapshot_id,
        "effective_base_snapshot_id": admin_snapshot_id or env_snapshot_id,
        "base_snapshot_source": source,
        "updated_at": value.get("updated_at"),
        "updated_by": value.get("updated_by"),
    }


async def upsert_sandbox_settings(
    update: SandboxSettingsUpdate, updated_by: str | None = None
) -> dict[str, Any]:
    await put_value(
        SANDBOX_SETTINGS_NAMESPACE,
        SANDBOX_SETTINGS_KEY,
        {
            "base_snapshot_id": update.base_snapshot_id,
            "updated_at": now_iso(),
            "updated_by": updated_by,
        },
    )
    return await get_sandbox_settings()
