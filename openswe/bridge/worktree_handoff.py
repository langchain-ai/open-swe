"""Move a desktop thread out of the user's checkout into a worktree of its own."""

import logging

from pydantic import JsonValue

from openswe.bridge.backend import BridgeSandboxBackend
from openswe.bridge.protocol import WorktreeHandoffParams
from openswe.run_config import RunConfig
from openswe.sandboxes.paths import forget_work_dir
from openswe.sandboxes.state import SANDBOX_BACKENDS, SandboxUnreachableError

logger = logging.getLogger(__name__)

_UNCONFIRMED = (
    "The desktop app did not confirm the handoff, but it may still have moved the thread. "
    "Run `pwd` and `git branch --show-current` before doing anything else."
)


async def worktree_handoff(
    branch: str,
    base_ref: str | None = None,
    start_from_origin: bool = True,
    user_checkout: bool = False,
) -> dict[str, JsonValue]:
    """Implement the `worktree_handoff` tool."""
    thread_id = RunConfig.from_runtime().thread_id
    proxy = SANDBOX_BACKENDS.get(thread_id) if thread_id else None
    backend = proxy.current if proxy is not None else None
    if not isinstance(backend, BridgeSandboxBackend):
        return {"success": False, "error": "This thread is not running on the desktop app."}
    params = WorktreeHandoffParams(
        branch=branch,
        base_ref=base_ref,
        start_from_origin=start_from_origin,
        user_checkout=user_checkout,
    )
    try:
        result = await backend.ahandoff_worktree(params)
    except TimeoutError, SandboxUnreachableError:
        logger.warning(
            "Worktree handoff went unconfirmed", extra={"agent_thread_id": thread_id}, exc_info=True
        )
        return {"success": False, "error": _UNCONFIRMED}
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}
    finally:
        forget_work_dir(proxy)
        forget_work_dir(backend)
    return {"success": True, **result}
