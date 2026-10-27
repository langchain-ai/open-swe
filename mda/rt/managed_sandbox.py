"""The thread's box as MDA's own sandbox: declared, opened before the run, replaced on request.

The graph declares one box (``BOX``) for every run; each run's catalog says which
box it uses and the options MDA creates it with (``SandboxSpec``), or that it has
none. MDA keeps one box per thread whatever the backend's options become, which
apply only when MDA creates it, and leaves its proxy config to the backend. Before the
run prepares, the box is opened and its name reported, so the backend binds the
thread to it. ``recreate_sandbox`` on such a box comes back from the backend as
what to replace it with; MDA replaces it, the backend reconnects, and the call
answers as it does in-process.
"""

import asyncio
import json
import logging
import os
import random
import re
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Final

import httpx
import httpx2
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from langsmith.sandbox import (
    QuotaExceededError,
    SandboxAuthenticationError,
    SandboxConnectionError,
)
from langsmith.sandbox import (
    ValidationError as SandboxValidationError,
)
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


BOX: Final = define_sandbox(
    _openswe_per_thread=True,
    _openswe_no_manage_proxy=True,
    _openswe_select=_selected,
    default_timeout=EXECUTE_TIMEOUT_SECONDS,
)


class SandboxOpenError(RuntimeError):
    """A sandbox open failed with a reason safe to send to the backend."""


def _open_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (SandboxAuthenticationError, SandboxValidationError, QuotaExceededError)):
        return False
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    if isinstance(exc, (SandboxConnectionError, httpx.TransportError, httpx2.TransportError)):
        return True
    return exc.__cause__ is not None and _open_retryable(exc.__cause__)


def _open_reason(exc: Exception) -> str:
    text = str(exc)
    for key, value in os.environ.items():
        if len(value) >= 8 and any(
            part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")
        ):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"https?://[^\s\"'<>]+", "[url redacted]", text)
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "[redacted]", text)
    text = re.sub(
        r"(?i)(?:(?:password|secret|api[_-]?key|(?:access[_-]?)?token|authorization)"
        r"[\"']?\s*[:=]\s*[\"']?[^\s,;\"']+|(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|lsv2_pt_)[\w-]+)",
        "[redacted]",
        text,
    )
    return f"{type(exc).__name__}: {' '.join(text.split())}"[:500]


async def opened_sandbox(runtime: Runtime) -> str | None:
    """Open the thread's box, retrying transient failures within a one-minute budget."""
    deadline = monotonic() + 60
    attempt = 0
    while True:
        try:
            return await open_sandbox(getattr(runtime, "backend", None))
        except Exception as exc:
            remaining = deadline - monotonic()
            reason = _open_reason(exc)
            if not _open_retryable(exc) or remaining <= 0:
                logger.warning("Could not open the thread's sandbox", extra={"reason": reason})
                raise SandboxOpenError(reason) from None
            delay = min(0.5 * 2 ** min(attempt, 5), 8) * random.uniform(0.8, 1.2)
            attempt += 1
            logger.warning("Retrying sandbox open", extra={"attempt": attempt, "reason": reason})
            await asyncio.sleep(min(delay, remaining))
            if monotonic() >= deadline:
                raise SandboxOpenError(reason) from None


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
