"""Admin-thread tools for managing environments.

Wired into the agent only for admin threads (see ``agent/server.py``). Every tool
re-checks the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

import logging
from typing import Any

from ..dashboard import environments as store
from .admin_gate import configurable as _configurable
from .admin_gate import require_admin

logger = logging.getLogger(__name__)


def _require_admin() -> str | None:
    return require_admin("manage environments")


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": record.get("name"),
        "slug": record.get("slug"),
        "prompt": record.get("prompt"),
        "repos": record.get("repos") or [],
        "mem_bytes": record.get("mem_bytes"),
        "vcpus": record.get("vcpus"),
        "fs_capacity_bytes": record.get("fs_capacity_bytes"),
        "create_params": record.get("create_params") or {},
        "snapshot_status": record.get("snapshot_status"),
        "snapshot_id": record.get("snapshot_id"),
        "snapshot_name": record.get("snapshot_name"),
        "status_message": record.get("status_message"),
        "last_captured_at": record.get("last_captured_at"),
    }


async def list_environments() -> dict[str, Any]:
    """List every environment with its snapshot state.

    The one named ``default`` is what runs boot from; the rest are drafts.

    Returns:
        ``{"ok": True, "environments": [...]}``.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    records = await store.list_environments()
    return {
        "ok": True,
        "environments": [
            {**_summary(record), "is_default": record.get("slug") == store.DEFAULT_ENVIRONMENT_SLUG}
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
    """Create an environment, or update an existing one's configuration.

    Does not touch the environment's snapshot: capture that separately with
    ``capture_environment_snapshot`` once this sandbox is provisioned.

    Args:
        name: Display name. Also the snapshot name stem, so keep it short and
            hyphenated (``langsmith-monorepo``). Saving under an existing name
            updates that environment rather than creating a second one. The name
            ``default`` is the environment every run boots from; any other name
            is a draft nobody boots from.
        prompt: The complete instruction text appended to every run's system
            prompt in this environment. This is a full replacement — pass the
            whole text, not a delta. Empty string clears it.
        repos: Optional ``owner/repo`` list this environment covers, for the
            dashboard. Does not clone anything by itself.
        mem_bytes: Optional memory capacity for newly-created sandbox VMs.
        vcpus: Optional virtual CPU count for newly-created sandbox VMs.
        fs_capacity_bytes: Optional filesystem capacity for newly-created sandbox VMs.
            Omitted sizing fields keep provider defaults when creating an environment,
            or preserve the existing values when updating one.
        clear_sizing: Restore provider defaults by clearing all three sizing overrides.
            Cannot be combined with a sizing value.
        create_params: Additional LangSmith sandbox create-body fields, such as
            ``_internal_runtime`` or ``proxy_config``. This object is persisted and
            must never contain secrets or authentication credentials. Omit it when
            updating to preserve the existing object.
        clear_create_params: Clear all additional create parameters. Cannot be combined
            with ``create_params``.

    Returns:
        ``{"ok": True, "environment": {...}, "created": bool}``.
    """
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

    existing = await store.get_environment(slug)
    try:
        if existing is None:
            login = _configurable().get("github_login")
            record = await store.create_environment(
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
            record = await store.update_environment(
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
    """Snapshot this sandbox as the named environment's image.

    Everything currently on this sandbox's filesystem is captured, so provision it
    fully first (clone the repos, install toolchains, warm caches) and leave no
    secrets or tokens on disk. The snapshot replaces the environment's previous
    one once it is ready. Capture takes minutes on a large filesystem.

    New sandboxes boot from it only for the environment named ``default``.

    Args:
        name: Name of an environment already saved with ``save_environment``.

    Returns:
        ``{"ok": True, "environment": {...}}`` with the new snapshot id.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if await store.get_environment(slug) is None:
        return {"ok": False, "error": f"no environment named {name!r}; call save_environment first"}

    thread_id = _configurable().get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"ok": False, "error": "no thread_id in the current run config"}

    try:
        from ..utils.sandbox_state import get_sandbox_backend, unwrap_sandbox_backend

        # ready() reconnects through the provider, which starts a stopped/idle box
        # before handing it back — so the capture always targets a running sandbox.
        backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
        record = await store.capture_environment_snapshot(slug, backend.id)
    except Exception as exc:
        logger.exception("Failed to capture snapshot for environment %s", slug)
        return {"ok": False, "error": f"snapshot capture failed: {exc}"}

    return {"ok": True, "environment": _summary(record)}


async def delete_environment(name: str) -> dict[str, Any]:
    """Delete an environment and its snapshot.

    Deleting ``default`` sends runs back to the per-repo and base snapshots.
    Confirm with the user first: the snapshot cannot be recovered, only rebuilt.

    Args:
        name: Environment to delete.

    Returns:
        ``{"ok": True, "deleted": bool}``.
    """
    if error := _require_admin():
        return {"ok": False, "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        deleted = await store.delete_environment(slug)
    except Exception as exc:
        logger.exception("Failed to delete environment %s", slug)
        return {"ok": False, "error": f"failed to delete environment: {exc}"}
    if not deleted:
        return {"ok": False, "error": f"no environment named {name!r}"}
    return {"ok": True, "deleted": True}
