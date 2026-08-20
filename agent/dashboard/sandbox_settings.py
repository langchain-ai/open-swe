"""Instance-wide sandbox settings an admin can change at runtime.

New sandboxes boot from a base snapshot, normally configured with the
``DEFAULT_SANDBOX_SNAPSHOT_ID`` env var. Admins can override it from the
dashboard so rolling out a rebuilt base image does not require a redeploy: the
stored value wins, and an unset record falls back to the env var.

The value is an opaque provider-scoped identifier — for ``SANDBOX_TYPE=langsmith``
it is a LangSmith snapshot id — so it is stored as free text with no format
validation. Per-repo snapshots (:mod:`agent.dashboard.repo_snapshots`) still take
precedence over this base for runs that target a repo with a ready snapshot.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

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


def _client():
    return get_client()


def env_base_snapshot_id() -> str | None:
    value = os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID", "").strip()
    return value or None


async def get_admin_base_snapshot_id() -> str | None:
    """Return the admin-configured base snapshot, ignoring the env default.

    Never raises: a store failure resolves to ``None`` so sandbox creation falls
    back to ``DEFAULT_SANDBOX_SNAPSHOT_ID``.
    """
    try:
        item = await _client().store.get_item(SANDBOX_SETTINGS_NAMESPACE, SANDBOX_SETTINGS_KEY)
    except Exception as e:  # noqa: BLE001
        logger.debug("sandbox settings lookup failed: %s", e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return None
    snapshot_id = value.get("base_snapshot_id")
    if not isinstance(snapshot_id, str):
        return None
    return snapshot_id.strip() or None


async def resolve_base_snapshot_id() -> str | None:
    """Return the base snapshot new sandboxes boot from: admin setting, else env."""
    return await get_admin_base_snapshot_id() or env_base_snapshot_id()


async def get_sandbox_settings() -> dict[str, Any]:
    """Return the stored settings plus the resolved effective base snapshot."""
    try:
        item = await _client().store.get_item(SANDBOX_SETTINGS_NAMESPACE, SANDBOX_SETTINGS_KEY)
    except Exception as e:  # noqa: BLE001
        logger.debug("sandbox settings lookup failed: %s", e)
        item = None
    value = {}
    if item is not None:
        stored = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(stored, dict):
            value = stored
    admin_snapshot_id = value.get("base_snapshot_id")
    admin_snapshot_id = (
        admin_snapshot_id.strip() or None if isinstance(admin_snapshot_id, str) else None
    )
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
    await _client().store.put_item(
        SANDBOX_SETTINGS_NAMESPACE,
        SANDBOX_SETTINGS_KEY,
        {
            "base_snapshot_id": update.base_snapshot_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": updated_by,
        },
    )
    return await get_sandbox_settings()
