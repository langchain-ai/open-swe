"""Admin-thread tools for managing environments.

Wired into the agent only for admin threads (see ``agent/server.py``). Every tool
re-checks the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

import logging
from typing import Any

from agent.dashboard import environments as store
from agent.tools.admin_gate import configurable as _configurable
from agent.tools.admin_gate import require_admin

logger = logging.getLogger(__name__)


def _require_admin() -> str | None:
    return require_admin("manage environments")


# Deliberately narrower than the record: the agent has no use for authorship
# or the source sandbox.
_SUMMARY_FIELDS = {
    "name",
    "slug",
    "prompt",
    "repos",
    "mem_bytes",
    "vcpus",
    "fs_capacity_bytes",
    "create_params",
    "snapshot_status",
    "snapshot_id",
    "snapshot_name",
    "status_message",
    "last_captured_at",
}


def _summary(record: store.Environment) -> dict[str, Any]:
    return record.model_dump(mode="json", include=_SUMMARY_FIELDS)


async def list_environments() -> dict[str, Any]:
    """Implement the `list_environments` tool."""
    if error := _require_admin():
        return {"ok": False, "error": error}
    records = await store.ENVIRONMENTS.list_all()
    return {
        "ok": True,
        "environments": [
            {**_summary(record), "is_default": record.slug == store.DEFAULT_ENVIRONMENT_SLUG}
            for record in records
        ],
    }


async def save_environment(
    name: str,
    prompt: str,
    repos: list[str] | None = None,
    mem_bytes: int | None = None,
    vcpus: int | None = None,
    fs_capacity_bytes: int | None = None,
    clear_sizing: bool = False,
    create_params: dict[str, Any] | None = None,
    clear_create_params: bool = False,
) -> dict[str, Any]:
    """Implement the `save_environment` tool."""
    if error := _require_admin():
        return {"ok": False, "error": error}
    sizing = {
        "mem_bytes": mem_bytes,
        "vcpus": vcpus,
        "fs_capacity_bytes": fs_capacity_bytes,
    }
    if clear_sizing and any(value is not None for value in sizing.values()):
        return {"ok": False, "error": "clear_sizing cannot be combined with sizing values"}
    if clear_create_params and create_params is not None:
        return {"ok": False, "error": "clear_create_params cannot be combined with create_params"}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    existing = await store.ENVIRONMENTS.get(slug)
    try:
        if existing is None:
            login = _configurable().github_login
            record = await store.ENVIRONMENTS.create(
                store.EnvironmentCreate(
                    name=name,
                    prompt=prompt,
                    repos=repos or [],
                    mem_bytes=mem_bytes,
                    vcpus=vcpus,
                    fs_capacity_bytes=fs_capacity_bytes,
                    create_params=create_params or {},
                ),
                login if isinstance(login, str) else "open-swe",
            )
        else:
            update_values: dict[str, Any] = {"name": name, "prompt": prompt, "repos": repos}
            update_values.update(
                dict.fromkeys(sizing)
                if clear_sizing
                else {field: value for field, value in sizing.items() if value is not None}
            )
            if create_params is not None:
                update_values["create_params"] = create_params
            elif clear_create_params:
                update_values["create_params"] = {}
            record = await store.ENVIRONMENTS.apply_update(
                slug,
                store.EnvironmentUpdate(**update_values),
            )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to save environment %s", slug)
        return {"ok": False, "error": f"failed to save environment: {exc}"}

    return {"ok": True, "environment": _summary(record), "created": existing is None}


async def capture_environment_snapshot(name: str) -> dict[str, Any]:
    """Implement the `capture_environment_snapshot` tool."""
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if await store.ENVIRONMENTS.get(slug) is None:
        return {"ok": False, "error": f"no environment named {name!r}; call save_environment first"}

    thread_id = _configurable().thread_id
    if not thread_id:
        return {"ok": False, "error": "no thread_id in the current run config"}

    try:
        from agent.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend

        # ready() reconnects through the provider, which starts a stopped/idle box
        # before handing it back — so the capture always targets a running sandbox.
        backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
        record = await store.capture_environment_snapshot(slug, backend.id)
    except Exception as exc:
        logger.exception("Failed to capture snapshot for environment %s", slug)
        return {"ok": False, "error": f"snapshot capture failed: {exc}"}

    return {"ok": True, "environment": _summary(record)}


async def delete_environment(name: str) -> dict[str, Any]:
    """Implement the `delete_environment` tool."""
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        deleted = await store.ENVIRONMENTS.remove(slug)
    except Exception as exc:
        logger.exception("Failed to delete environment %s", slug)
        return {"ok": False, "error": f"failed to delete environment: {exc}"}
    if not deleted:
        return {"ok": False, "error": f"no environment named {name!r}"}
    return {"ok": True, "deleted": True}
