"""The thread's box as MDA's own sandbox: declared, opened before the run, replaced on request.

The graph declares one box (``box``) for every run; each run's catalog says which
box it uses and the options MDA creates it with (``SandboxSpec``), or that it has
none, when what the graph keeps itself, such as large tool results, goes to the
backend's files instead. MDA keeps one box per thread whatever the backend's options
become, which apply only when MDA creates it, and leaves its proxy config to the
backend. Before the run prepares, the box is opened and its name reported, so the
backend binds the thread to it. ``recreate_sandbox`` on such a box comes back from the backend as
what to replace it with; MDA replaces it, the backend reconnects, and the call
answers as it does in-process.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from managed_deepagents import define_sandbox
from managed_deepagents.sandbox import SandboxDefinition, open_sandbox, replace_sandbox
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.catalog import SandboxCreate
from runtime_api.endpoint import call
from runtime_api.loop import SANDBOX_REPLACED
from runtime_api.operations import RunRequest

from rt.backend import BackendCallError, OpenSweBackend, RunContext, current_run
from rt.tools import run_id

logger = logging.getLogger(__name__)

_json_object = TypeAdapter(dict[str, JsonValue])
# As the backend's sandbox: a command without a timeout of its own gets five minutes.
EXECUTE_TIMEOUT_SECONDS: Final = 300


def _selected(runtime: object) -> SandboxDefinition | None:
    """The box the run in ``runtime`` uses, as its catalog says; None without one of MDA's."""
    context = getattr(runtime, "context", None)
    try:
        # A hook's backend is bound to the run's configurable, not its runtime, so it
        # carries no run context: the run in progress is the one it acts for.
        run = context.run() if isinstance(context, RunContext) else current_run()
        spec = run.planned.sandbox
    except BackendCallError:
        # Not started yet, or not an Open SWE run, so nothing may open a box for it.
        return None
    return None if spec is None else definition(spec.create, spec.thread_id)


def definition(create: SandboxCreate, thread_id: str | None) -> SandboxDefinition:
    """The box MDA keeps for ``thread_id`` (the run's own by default), created from ``create``."""
    return define_sandbox(
        _openswe_per_thread=True,
        _openswe_thread_id=thread_id,
        _openswe_no_manage_proxy=True,
        _openswe_create_fields=dict(create.extra_fields),
        default_timeout=EXECUTE_TIMEOUT_SECONDS,
        snapshot_id=create.snapshot_id,
        idle_ttl_seconds=create.idle_ttl_seconds,
        delete_after_stop_seconds=create.delete_after_stop_seconds,
        fs_capacity_bytes=create.fs_capacity_bytes,
        vcpus=create.vcpus,
        mem_bytes=create.mem_bytes,
    )


def box(without_box: SandboxBackendProtocol) -> SandboxDefinition:
    """The box every run's catalog picks from, and ``without_box`` for a run it gives none."""
    return define_sandbox(
        _openswe_per_thread=True,
        _openswe_no_manage_proxy=True,
        _openswe_select=_selected,
        _openswe_without_box=without_box,
        default_timeout=EXECUTE_TIMEOUT_SECONDS,
    )


async def opened_sandbox(runtime: Runtime) -> str | None:
    """Adopt or create the thread's box now; its name, or None when MDA could not open it.

    The backend reports a box MDA could not open as unreachable when it prepares.
    """
    try:
        return await open_sandbox(getattr(runtime, "backend", None))
    except Exception:
        logger.warning("Could not open the thread's sandbox", exc_info=True)
        return None


class ReplaceSandboxMiddleware(AgentMiddleware):
    """Carry out ``recreate_sandbox`` for the box MDA owns."""

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if request.tool_call["name"] != "recreate_sandbox" or not isinstance(result, ToolMessage):
            return result
        spec = self._backend.run.planned.sandbox
        if spec is None:
            return result
        try:
            reply = _json_object.validate_json(result.text)
            create = SandboxCreate.model_validate(reply.get("replace"))
        except ValidationError:
            return result
        try:
            replacement = await replace_sandbox(
                getattr(request.runtime, "backend", None), definition(create, spec.thread_id)
            )
            await call(self._backend, SANDBOX_REPLACED, RunRequest(run_id=run_id()))
        except Exception as exc:
            logger.warning("Could not replace the thread's sandbox", exc_info=True)
            answer: dict[str, JsonValue] = {"success": False, "error": str(exc)}
        else:
            answer = {
                "success": True,
                "old_sandbox_id": replacement.old_name,
                "new_sandbox_id": replacement.new_name,
                "old_sandbox_stopped": replacement.old_stopped,
                "old_sandbox_stop_error": replacement.stop_error,
            }
        return result.model_copy(update={"content": json.dumps(answer)})
