"""The thread's box as MDA's own sandbox: declared, opened before the run, replaced on request.

MDA keeps one box per thread whatever the backend's options become, which apply
only when MDA creates it, and leaves its proxy config to the backend. Before the
run prepares, the box is opened and its name reported, so the backend binds the
thread to it. ``recreate_sandbox`` on such a box comes back from the backend as
what to replace it with; MDA replaces it, the backend reconnects, and the call
answers as it does in-process.
"""

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Final

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from managed_deepagents import define_sandbox
from managed_deepagents.sandbox import SandboxDefinition, open_sandbox, replace_sandbox
from pydantic import JsonValue, TypeAdapter, ValidationError

from rt.backend import OpenSweBackend, run_body
from rt.sandbox import SandboxCreate, SandboxSpec
from rt.tools import run_id

logger = logging.getLogger(__name__)

_json_object = TypeAdapter(dict[str, JsonValue])
# As the backend's sandbox: a command without a timeout of its own gets five minutes.
EXECUTE_TIMEOUT_SECONDS: Final = 300
# The backend's own names for the new sandbox region, when it is not the deployment's LangSmith.
_SANDBOX_ENDPOINT_ENV: Final = "NEW_SANDBOX_REGION_ENDPOINT"
_SANDBOX_API_KEY_ENV: Final = "NEW_SANDBOX_REGION_API_KEY"
_SANDBOX_API_SUFFIX: Final = "/v2/sandboxes"


def definition(spec: SandboxSpec, create: SandboxCreate | None = None) -> SandboxDefinition:
    """The box MDA keeps for the spec's thread, created from ``create`` (the spec's by default)."""
    create = create or spec.create
    endpoint, api_key = _sandbox_langsmith(spec.api)
    return define_sandbox(
        _openswe_per_thread=True,
        _openswe_thread_id=spec.thread_id,
        _openswe_no_manage_proxy=True,
        _openswe_create_fields=dict(create.extra_fields),
        api_key=api_key,
        api_endpoint=endpoint,
        default_timeout=EXECUTE_TIMEOUT_SECONDS,
        snapshot_id=create.snapshot_id,
        idle_ttl_seconds=create.idle_ttl_seconds,
        delete_after_stop_seconds=create.delete_after_stop_seconds,
        fs_capacity_bytes=create.fs_capacity_bytes,
        vcpus=create.vcpus,
        mem_bytes=create.mem_bytes,
    )


def _sandbox_langsmith(api_root: str | None) -> tuple[str | None, str | None]:
    """The sandbox API and key of the LangSmith the backend says the box lives at.

    ``None`` is this deployment's own, which needs neither. Any other must be the one
    new sandbox region, the only other LangSmith this deployment holds a key for.
    """
    if api_root is None:
        return None, None
    endpoint = _root(os.environ.get(_SANDBOX_ENDPOINT_ENV, ""))
    api_key = os.environ.get(_SANDBOX_API_KEY_ENV, "").strip()
    if endpoint != _root(api_root) or not api_key:
        msg = (
            f"The thread's sandbox lives at {api_root}, but this deployment's "
            f"{_SANDBOX_ENDPOINT_ENV} and {_SANDBOX_API_KEY_ENV} do not name it"
        )
        raise RuntimeError(msg)
    return f"{endpoint}{_SANDBOX_API_SUFFIX}", api_key


def _root(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(_SANDBOX_API_SUFFIX)


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

    def __init__(self, backend: OpenSweBackend, spec: SandboxSpec) -> None:
        self._backend = backend
        self._spec = spec

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if request.tool_call["name"] != "recreate_sandbox" or not isinstance(result, ToolMessage):
            return result
        try:
            reply = _json_object.validate_json(result.text)
            create = SandboxCreate.model_validate(reply.get("replace"))
        except ValidationError:
            return result
        try:
            replacement = await replace_sandbox(
                getattr(request.runtime, "backend", None), definition(self._spec, create)
            )
            await self._backend.call("sandbox", run_body(run_id(), request.state))
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
