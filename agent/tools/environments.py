"""Admin-thread tools for managing environments.

Wired into the agent only for admin threads (see ``agent/server.py``). Every tool
re-checks the triggering user against ``CONFIGURED_ADMINS`` so a thread whose
metadata says "admin" cannot act on behalf of someone who is not one.
"""

import logging
from typing import Any

from agent.dashboard import environment_refresh as refresh
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
    "setup_script",
    "update_script",
    "base_snapshot_id",
    "snapshot_status",
    "snapshot_id",
    "snapshot_name",
    "snapshot_tag",
    "status_message",
    "last_captured_at",
    "refresh_status",
    "refresh_kind",
    "refresh_finished_at",
    "refresh_run_id",
    "refresh_error",
}


def _summary(record: store.Environment) -> dict[str, Any]:
    summary = record.model_dump(mode="json", include=_SUMMARY_FIELDS)
    summary["refresh_log_excerpt"] = store.log_excerpt(record.refresh_log)
    return summary


async def _start_refresh(slug: str, name: str) -> dict[str, Any]:
    """Enqueue a refresh and describe the handle, or say why it cannot start."""
    record = await store.ENVIRONMENTS.get(slug)
    if record is None:
        return {
            "status": "error",
            "error": f"no environment named {name!r}; publish_environment creates one",
        }
    if not record.setup_script:
        return {"status": "error", "error": f"environment {name!r} has no setup_script to run"}
    if refresh.is_refresh_in_flight(record):
        return {
            "status": "error",
            "error": f"a refresh of {name!r} is already running",
            "task_id": refresh.refresh_task_id(record.refresh_run_id or ""),
        }

    run_id = await refresh.start_refresh_run(slug)
    if run_id is None:
        return {"status": "error", "error": "could not start the refresh job"}
    return {"status": "started", "task_id": refresh.refresh_task_id(run_id)}


async def list_environments() -> dict[str, Any]:
    """List every environment with its snapshot state.

    The one named ``default`` is what runs boot from; the rest are drafts.

    Returns:
        ``{"ok": True, "environments": [...]}``.
    """
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


async def publish_environment(
    name: str,
    prompt: str,
    setup_script: str | None = None,
    update_script: str | None = None,
    base_snapshot_id: str | None = None,
    clear_base_snapshot_id: bool = False,
    snapshot_name: str | None = None,
    repos: list[str] | None = None,
    mem_bytes: int | None = None,
    vcpus: int | None = None,
    fs_capacity_bytes: int | None = None,
    clear_sizing: bool = False,
    create_params: dict[str, Any] | None = None,
    clear_create_params: bool = False,
) -> dict[str, Any]:
    """Implement the `publish_environment` tool."""
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
    if clear_base_snapshot_id and base_snapshot_id is not None:
        return {
            "ok": False,
            "error": "clear_base_snapshot_id cannot be combined with base_snapshot_id",
        }
    thread_id = _configurable().thread_id
    if not thread_id:
        return {"ok": False, "error": "no thread_id in the current run config"}

    # Validate the whole definition before capturing, so a bad name or script
    # limit is refused in milliseconds rather than after minutes of capture.
    try:
        slug = store.slugify(name)
        existing = await store.ENVIRONMENTS.get(slug)
        definition: store.EnvironmentCreate | store.EnvironmentUpdate
        if existing is None:
            definition = store.EnvironmentCreate(
                name=name,
                prompt=prompt,
                setup_script=setup_script or "",
                update_script=update_script or "",
                base_snapshot_id=base_snapshot_id,
                snapshot_name=snapshot_name,
                repos=repos or [],
                mem_bytes=mem_bytes,
                vcpus=vcpus,
                fs_capacity_bytes=fs_capacity_bytes,
                create_params=create_params or {},
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
            if setup_script is not None:
                update_values["setup_script"] = setup_script
            if update_script is not None:
                update_values["update_script"] = update_script
            if snapshot_name is not None:
                update_values["snapshot_name"] = snapshot_name
            if base_snapshot_id is not None:
                update_values["base_snapshot_id"] = base_snapshot_id
            elif clear_base_snapshot_id:
                update_values["base_snapshot_id"] = None
            definition = store.EnvironmentUpdate(**update_values)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    published_name = snapshot_name or (
        existing.published_snapshot_name
        if existing is not None
        else store.default_snapshot_name_for(slug)
    )
    try:
        from agent.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend

        # ready() reconnects through the provider, which starts a stopped/idle box
        # before handing it back — so the capture always targets a running sandbox.
        backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
        snapshot_id = await store.capture_sandbox_snapshot(
            backend.id, published_name, timeout=refresh.capture_timeout()
        )
    except Exception as exc:
        logger.exception("Failed to capture sandbox for environment %s", slug)
        return {"ok": False, "error": f"snapshot capture failed: {exc}"}

    # Definition and image pointer land in one write, so a failure here means
    # nothing was written — and the image nothing points at is discarded.
    login = _configurable().github_login
    try:
        record = await store.ENVIRONMENTS.publish(
            slug,
            definition,
            snapshot_id=snapshot_id,
            snapshot_name=published_name,
            source_sandbox_id=backend.id,
            created_by=login if isinstance(login, str) else "open-swe",
        )
    except Exception as exc:
        logger.exception("Failed to record environment %s after capture", slug)
        await store.discard_unreferenced_snapshot(slug, snapshot_id)
        return {"ok": False, "error": f"failed to record the environment: {exc}"}

    await store.retire_superseded_snapshot(
        slug, existing.snapshot_id if existing is not None else None, snapshot_id
    )
    if record.setup_script:
        await refresh.ensure_refresh_cron(slug)
    return {"ok": True, "environment": _summary(record), "created": existing is None}


async def refresh_environment_start(name: str) -> dict[str, Any]:
    """Implement the `refresh_environment_start` tool."""
    if error := _require_admin():
        return {"status": "error", "error": error}
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    return await _start_refresh(slug, name)


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
