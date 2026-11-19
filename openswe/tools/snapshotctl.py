"""Authorized tools for Robin's global execution image."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from openswe.audit_logs.store import list_logs
from openswe.audit_logs.tools import audit_tool
from openswe.execution_images import EXECUTION_IMAGES
from openswe.sandboxes.image_build import RefreshKind
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

_PRIVATE_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("task_id"))
_READS = frozenset({"status", "history"})


@expose_mcp(access="admin")
@audit_tool(reads=_READS)
@access(_WRITE, per_call=lambda args: _PRIVATE_READ if args.get("action") in _READS else _WRITE)
async def snapshotctl(action: Literal["status", "stop", "history"]) -> dict[str, object]:
    """Read global image diagnostics and history, or stop its current build."""
    if action == "status":
        return {"ok": True, **await EXECUTION_IMAGES.status()}
    if action == "stop":
        state = await EXECUTION_IMAGES.status(with_output=False)
        task_id = state.get("task_id")
        if not isinstance(task_id, str) or state.get("status") not in {"queued", "running"}:
            return {"ok": False, "error": "no global execution image build is running"}
        return {"ok": True, **await EXECUTION_IMAGES.task_stop(task_id)}
    now = datetime.now(UTC)
    entries = []
    for operation in ("snapshotctl", "rebuild_execution_image", "publish_execution_image"):
        page = await list_logs(
            start_time=now - timedelta(days=30),
            end_time=now,
            limit=25,
            operation_name=operation,
        )
        entries.extend(page.items)
    return {
        "ok": True,
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
            for entry in sorted(entries, key=lambda entry: entry.request_time, reverse=True)[:25]
        ],
    }


@expose_mcp(access="admin")
@access(_PRIVATE_READ)
async def read_execution_image() -> dict[str, object]:
    """Read the global execution image and its private refresh diagnostics."""
    return {"ok": True, **await EXECUTION_IMAGES.status()}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def rebuild_execution_image(kind: RefreshKind = "full") -> dict[str, object]:
    """Queue a global execution image rebuild or update."""
    try:
        run_id = await EXECUTION_IMAGES.start(kind)
        return {"ok": True, "status": "started", "task_id": f"image-{run_id}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def publish_execution_image() -> dict[str, object]:
    """Queue publication of this sandbox as the global image without stopping it."""
    thread_id = configurable().thread_id
    if not thread_id:
        return {"ok": False, "error": "no thread_id in the current run config"}
    from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend

    backend = unwrap_sandbox_backend(await get_sandbox_backend(thread_id))
    try:
        run_id = await EXECUTION_IMAGES.publish(str(backend.id))
        return {"ok": True, "status": "started", "task_id": f"image-{run_id}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
