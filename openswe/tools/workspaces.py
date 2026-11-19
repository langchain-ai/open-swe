"""Admin-thread tools for managing workspaces.

Wired into admin threads; each tool rechecks user or system authorization.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from openswe.audit_logs.store import list_logs
from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS
from openswe.execution_images import EXECUTION_IMAGES
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
    "slack_channel_ids",
    "kitchen_channel_ids",
    "ask_channel_ids",
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


@expose_mcp(access="admin")
@audit_tool(target_workspace="workspace")
@access(_WRITE)
async def configure_workspace(
    workspace: str,
    changes: store.WorkspaceRoutingUpdate,
) -> dict[str, object]:
    """Update routing bindings for a retained workspace."""
    from openswe.workspaces.configuration import configure_workspace as save_configuration

    try:
        slug = store.slugify(workspace)
        previous = await store.WORKSPACES.get(slug)
        if previous is None:
            raise ValueError(f"no workspace named {slug!r}")
        record = await save_configuration(previous, changes)
    except (ValueError, refresh.RefreshStartError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "workspace": _summary(record)}


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
    action: Literal["status", "stop", "history"],
    workspace: str,
) -> dict[str, object]:
    """Read legacy image diagnostics or stop its running refresh."""
    try:
        record = await store.WORKSPACES.get(store.slugify(workspace))
        if record is None:
            raise ValueError(f"no workspace named {workspace!r}")
        if action == "status":
            return {"ok": True, **await _snapshot_state(record)}
        if action == "stop":
            return await _stop_refresh(record)
        return await _history(record)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@expose_mcp(access="admin")
@access(_PRIVATE_READ)
async def read_execution_image() -> dict[str, object]:
    """Read the global execution image and its private refresh diagnostics."""
    return {"ok": True, **await EXECUTION_IMAGES.status()}


@expose_mcp(access="admin")
@audit_tool()
@access(Policy(trusted="admin_thread", actor="admin", sole=ack("task_id")))
async def rebuild_execution_image(kind: store.RefreshKind = "full") -> dict[str, object]:
    """Queue a global execution image rebuild without changing workspace routing."""
    try:
        run_id = await EXECUTION_IMAGES.start(kind)
        return {"ok": True, "status": "started", "task_id": f"image-{run_id}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@expose_mcp(access="admin")
@audit_tool()
@access(Policy(trusted="admin_thread", actor="admin", sole=ack("task_id")))
async def publish_execution_image() -> dict[str, object]:
    """Queue publication of this sandbox as the global image without binding repositories."""
    if not await EXECUTION_DEFAULTS.is_enabled():
        return {
            "ok": False,
            "error": "global execution defaults are disabled; enable Use Robin-wide Defaults before publishing",
        }
    thread_id = _configurable().thread_id
    if not thread_id:
        return {"ok": False, "error": "no thread_id in the current run config"}
    from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend

    backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
    try:
        run_id = await EXECUTION_IMAGES.publish(str(backend.id))
        return {"ok": True, "status": "started", "task_id": f"image-{run_id}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


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
