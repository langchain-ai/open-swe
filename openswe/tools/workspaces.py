"""Admin-thread tools for managing workspaces.

Wired into admin threads; each tool rechecks user or system authorization.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import JsonValue

from openswe.audit_logs.store import list_logs
from openswe.audit_logs.tools import audit_tool
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable as _configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.workspaces import refresh, store

logger = logging.getLogger(__name__)


_READ = Policy(trusted="admin_thread", actor="admin")
_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("workspace.slug", "task_id"))
# Refresh logs are ``bash -x`` traces and history names people, so neither is
# read where anyone else can see the thread.
_PRIVATE_READ = Policy(trusted="admin_surface", actor="admin")

_SNAPSHOT_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("workspace", "task_id"))
_SNAPSHOT_READS = frozenset({"status", "history"})
_SNAPSHOT_STATE_FIELDS = {
    "snapshot_id",
    "snapshot_name",
    "snapshot_status",
    "status_message",
    "last_captured_at",
    "base_snapshot_id",
    "refresh_status",
    "refresh_kind",
    "refresh_run_id",
    "refresh_started_at",
    "refresh_finished_at",
    "refresh_steps",
    "refresh_error",
    "setup_script",
    "update_script",
    "create_params",
}
_HISTORY_WINDOW = timedelta(days=30)
_HISTORY_LIMIT = 25


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


def _summary(record: store.Workspace) -> dict[str, Any]:
    summary = record.model_dump(mode="json", include=_SUMMARY_FIELDS)
    summary["refresh_log_excerpt"] = store.log_excerpt(record.refresh_log)
    return summary


@expose_mcp(access="admin")
@access(_READ)
async def list_workspaces() -> dict[str, Any]:
    """List every workspace with its snapshot state.

    The one named ``default`` is what unrouted work falls back to.

    Returns:
        ``{"ok": True, "workspaces": [...]}``.
    """
    records = await store.WORKSPACES.list_all()
    return {
        "ok": True,
        "workspaces": [
            {**_summary(record), "is_default": record.slug == store.DEFAULT_WORKSPACE_SLUG}
            for record in records
        ],
    }


@audit_tool(target_workspace="name")
@access(_WRITE)
async def publish_workspace(
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
    """Implement the `publish_workspace` tool."""
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
        existing = await store.WORKSPACES.get(slug)
        definition: store.WorkspaceCreate | store.WorkspaceUpdate
        if existing is None:
            definition = store.WorkspaceCreate(
                name=name,
                inherit_default_sandbox=False,
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
            update_values: dict[str, Any] = {
                "name": name,
                "prompt": prompt,
                "repos": repos,
                "inherit_default_sandbox": False,
            }
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
            definition = store.WorkspaceUpdate(**update_values)
        # Repository ownership and the min-one-repo rule are checked here as
        # well as on the write: a capture takes minutes, and a definition that
        # can never be saved should be refused before it starts.
        await store.WORKSPACES.assert_publishable(slug, definition)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    published_name = snapshot_name or (
        existing.published_snapshot_name
        if existing is not None
        else store.default_snapshot_name_for(slug)
    )
    try:
        from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend

        # ready() reconnects through the provider, which starts a stopped/idle box
        # before handing it back — so the capture always targets a running sandbox.
        backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
        snapshot_id = await store.capture_sandbox_snapshot(
            backend.id, published_name, timeout=refresh.capture_timeout()
        )
    except Exception as exc:
        logger.exception("Failed to capture sandbox for workspace", extra={"workspace": slug})
        return {"ok": False, "error": f"snapshot capture failed: {exc}"}

    # Definition and image pointer land in one write, so a failure here means
    # nothing was written — and the image nothing points at is discarded.
    login = _configurable().github_login
    try:
        record = await store.WORKSPACES.publish(
            slug,
            definition,
            snapshot_id=snapshot_id,
            snapshot_name=published_name,
            source_sandbox_id=backend.id,
            created_by=login if isinstance(login, str) else "open-swe",
        )
    except Exception as exc:
        logger.exception("Failed to record workspace after capture", extra={"workspace": slug})
        await store.discard_unreferenced_snapshot(slug, snapshot_id)
        return {"ok": False, "error": f"failed to record the workspace: {exc}"}

    await store.retire_superseded_snapshot(
        slug, existing.snapshot_id if existing is not None else None, snapshot_id
    )
    if record.setup_script:
        await refresh.ensure_refresh_cron(slug)
    return {"ok": True, "workspace": _summary(record), "created": existing is None}


async def _snapshot_state(record: store.Workspace) -> dict[str, object]:
    """The image runs boot from, the refresh that last touched it, and its log."""
    state: dict[str, object] = record.model_dump(mode="json", include=_SNAPSHOT_STATE_FIELDS)
    state["workspace"] = record.slug
    state["refresh_in_flight"] = record.refresh_in_flight()
    state["refresh_task_id"] = (
        refresh.refresh_task_id(record.refresh_run_id) if record.refresh_run_id else None
    )
    state["refresh_log_tail"] = record.refresh_log_tail()
    state["live_log"] = await refresh.builder_trace(record)
    return state


async def _stop_refresh(record: store.Workspace) -> dict[str, object]:
    if record.refresh_status != "refreshing" or not record.refresh_run_id:
        raise ValueError(f"no refresh of {record.slug!r} is running")
    stopped = await refresh.task_stop(refresh.refresh_task_id(record.refresh_run_id))
    return {"ok": "error" not in stopped, **stopped}


async def _edit(record: store.Workspace, changes: dict[str, object]) -> dict[str, object]:
    if not changes:
        raise ValueError("edit needs setup_script, update_script or create_params")
    saved = await refresh.save_definition(record, store.WorkspaceUpdate.model_validate(changes))
    return {
        "ok": True,
        "workspace": record.slug,
        "previous": record.model_dump(mode="json", include=set(changes)),
        "current": saved.model_dump(mode="json", include=set(changes)),
    }


async def _history(record: store.Workspace) -> dict[str, object]:
    workspace_id = await store.WORKSPACES.id_for_slug(record.slug)
    if workspace_id is None:
        raise ValueError(f"no workspace named {record.slug!r}")
    now = datetime.now(UTC)
    page = await list_logs(
        start_time=now - _HISTORY_WINDOW,
        end_time=now,
        limit=_HISTORY_LIMIT,
        workspace_id=workspace_id,
    )
    return {
        "ok": True,
        "workspace": record.slug,
        "entries": [
            {
                "at": entry.request_time.isoformat(),
                "operation": entry.operation_name,
                "succeeded": entry.operation_succeeded,
                "actor": entry.enrichments.actor_login,
                "actor_kind": entry.enrichments.actor_kind,
                "source": entry.enrichments.source,
                "thread_id": entry.enrichments.thread_id,
            }
            for entry in page.items
        ],
    }


@expose_mcp(access="admin")
@audit_tool(reads=_SNAPSHOT_READS, target_workspace="workspace")
@access(
    _SNAPSHOT_WRITE,
    per_call=lambda args: (
        _PRIVATE_READ if args.get("action") in _SNAPSHOT_READS else _SNAPSHOT_WRITE
    ),
)
async def snapshotctl(
    action: Literal["status", "rebuild", "stop", "edit", "history"],
    workspace: str,
    kind: store.RefreshKind | None = None,
    setup_script: str | None = None,
    update_script: str | None = None,
    create_params: dict[str, JsonValue] | None = None,
) -> dict[str, object]:
    """Implement the `snapshotctl` tool."""
    changes: dict[str, object] = {
        field: value
        for field, value in (
            ("setup_script", setup_script),
            ("update_script", update_script),
            ("create_params", create_params),
        )
        if value is not None
    }
    try:
        if changes and action != "edit":
            raise ValueError(f"{', '.join(changes)} only apply to edit")
        if kind is not None and action != "rebuild":
            raise ValueError("kind only applies to rebuild")
        record = await store.WORKSPACES.get(store.slugify(workspace))
        if record is None:
            raise ValueError(f"no workspace named {workspace!r}")
        if action == "status":
            return {"ok": True, **await _snapshot_state(record)}
        if action == "rebuild":
            run_id = await refresh.start_refresh(record, kind or "full")
            return {
                "ok": True,
                "status": "started",
                "workspace": record.slug,
                "refresh_kind": kind or "full",
                "task_id": refresh.refresh_task_id(run_id),
            }
        if action == "stop":
            return await _stop_refresh(record)
        if action == "edit":
            return await _edit(record, changes)
        return await _history(record)
    except store.RefreshInFlightError as exc:
        running = refresh.refresh_task_id(exc.run_id) if exc.run_id else None
        return {"ok": False, "error": str(exc), "task_id": running}
    except (ValueError, refresh.RefreshStartError) as exc:
        return {"ok": False, "error": str(exc)}


@expose_mcp(access="admin")
@audit_tool(target_workspace="name")
@access(_WRITE)
async def delete_workspace(name: str) -> dict[str, Any]:
    """Implement the `delete_workspace` tool."""
    try:
        slug = store.slugify(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        deleted = await store.WORKSPACES.remove(slug)
    except store.DefaultWorkspaceDeletionError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to delete workspace", extra={"workspace": slug})
        return {"ok": False, "error": f"failed to delete workspace: {exc}"}
    if not deleted:
        return {"ok": False, "error": f"no workspace named {name!r}"}
    return {"ok": True, "deleted": True}


@expose_mcp(access="admin")
@audit_tool(target_workspace="workspace")
@access(_WRITE)
async def configure_repository(
    workspace: str,
    repo: str,
    may_start_threads: bool | None = None,
) -> dict[str, Any]:
    """Implement the `configure_repository` tool."""
    try:
        slug = store.slugify(workspace)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        settings = await store.WORKSPACES.configure_repository(
            slug, repo, may_start_threads=may_start_threads
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception(
            "Failed to configure repository",
            extra={"workspace": slug, "repository": repo},
        )
        return {"ok": False, "error": f"failed to configure repository: {exc}"}
    return {"ok": True, "workspace": slug, "repository": settings.model_dump()}
