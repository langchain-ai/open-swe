"""Move a desktop thread out of the user's checkout into a worktree of its own."""

from pydantic import JsonValue

from agent.bridge.backend import BridgeSandboxBackend
from agent.bridge.protocol import WorktreeHandoffParams
from agent.run_config import RunConfig
from agent.sandboxes.paths import forget_work_dir
from agent.sandboxes.state import SANDBOX_BACKENDS


async def worktree_handoff(
    branch: str, base_ref: str | None = None, start_from_origin: bool = True
) -> dict[str, JsonValue]:
    """Implement the `worktree_handoff` tool."""
    thread_id = RunConfig.from_runtime().thread_id
    proxy = SANDBOX_BACKENDS.get(thread_id) if thread_id else None
    backend = proxy.current if proxy is not None else None
    if not isinstance(backend, BridgeSandboxBackend):
        return {"success": False, "error": "This thread is not running on the desktop app."}
    params = WorktreeHandoffParams(
        branch=branch, base_ref=base_ref, start_from_origin=start_from_origin
    )
    try:
        result = await backend.ahandoff_worktree(params)
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}
    forget_work_dir(proxy)
    forget_work_dir(backend)
    return {"success": True, **result}
