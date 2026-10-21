"""The file and shell tools of the thread's box, run here with the graph's own checks.

Where MDA owns the thread's box, MDA's own file and shell tools serve the graph's
against it, with no call to the backend in between. This middleware applies to
those calls what the graph's tool-call middleware does in-process, as the backend's
catalog lists it: tool errors become results the model can act on, a file posing
as an image is not attached, and reading a file loads the ``AGENTS.md`` files above
it. The transcript hears of each call alongside it.

The backend still runs the calls only it can settle: calls on the paths it serves,
such as skills, and, where the graph guards pushes and pull requests, commands that
may push or open one. Those guards inspect the push and may need a person's
approval, so the match here is wider than what they block: nothing they would stop
runs in the box.
"""

import asyncio
import base64
import json
import logging
import posixpath
import random
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from functools import partial
from time import monotonic
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import (
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxRetryableConnectionError,
    SandboxServerReloadError,
)

from rt.backend import BackendCallError, OpenSweBackend, run_body
from rt.middleware import reported
from rt.sandbox import SandboxSpec
from rt.tools import run_id, run_on_backend

logger = logging.getLogger(__name__)

# The argument naming the path each file tool acts on.
_PATH_ARGUMENTS: Final = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "glob": "path",
    "grep": "path",
}
# A command the push and pull request guards may stop: one naming a push, the GitHub
# CLI or the pulls API, or nesting shells as deep as the pull request guard reads.
_GUARDED_WORDS: Final = re.compile(r"\b(?:push|gh|pulls)\b")
_SHELL: Final = re.compile(r"\b(?:ba|da|z)?sh\b")
_GUARDED_SHELLS: Final = 3
# As the backend's sandbox: the client gives up on a command the sandbox never
# answers 30 seconds past its own timeout.
_EXECUTE_GRACE_SECONDS: Final = 30
# A sandbox call rejected before it started cannot have run, so it is sent again.
_TRANSIENT_BACKOFF_SECONDS: Final = (0.5, 1.0, 2.0)
_SLOW_CALL_SECONDS: Final = 5
_SANDBOX_ID: Final = re.compile(r"\bsb-[A-Za-z0-9-]+\b")
_READ_FILE: Final = "read_file"
_AGENTS_MD: Final = "AGENTS.md"
_MAX_AGENTS_LINES: Final = 1_000
_MAX_AGENTS_BYTES: Final = 64 * 1024
_IMAGE_SIGNATURES: Final[tuple[tuple[bytes, ...], ...]] = (
    (b"\x89PNG\r\n\x1a\n",),
    (b"\xff\xd8\xff",),
    (b"GIF87a",),
    (b"GIF89a",),
    (b"RIFF", b"WEBP"),
)

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
Check = Callable[[ToolCallRequest, Handler], Awaitable[ToolMessage | Command]]


class SandboxToolsMiddleware(AgentMiddleware):
    """Run the box's file and shell tools here, and the calls only the backend can settle there."""

    def __init__(self, backend: OpenSweBackend, spec: SandboxSpec, execute_timeout: int) -> None:
        self._backend = backend
        self._tools = frozenset(spec.tools)
        self._guards_pushes = "push_guards" in spec.checks
        self._backend_paths = tuple(spec.backend_paths)
        self._execute_timeout = execute_timeout
        # The AGENTS.md files the model has seen; a read loads each one once.
        self._agents_seen: set[str] = set()
        # Outermost first, in the graph's own order.
        checks: Sequence[tuple[str, Check]] = (
            ("image_reads", _without_false_images),
            ("tool_errors", self._errors_as_results),
            ("agents_md", self._with_agents_md),
        )
        self._checks = [check for name, check in checks if name in spec.checks]

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        call = request.tool_call
        if call["name"] not in self._tools:
            return await handler(request)
        if self._on_backend(call["name"], call["args"]):
            return await run_on_backend(self._backend, call["name"], call["args"], request.runtime)
        checked = handler
        for check in reversed([*self._checks, _retried, self._within_deadline]):
            checked = partial(check, handler=checked)
        return await reported(self._backend, request, checked)

    def _on_backend(self, name: str, arguments: dict[str, object]) -> bool:
        if name == "execute":
            command = arguments.get("command")
            return self._guards_pushes and isinstance(command, str) and _guarded(command)
        path = _absolute(arguments.get(_PATH_ARGUMENTS.get(name, "")))
        return path is not None and any(
            path == prefix.rstrip("/") or path.startswith(prefix) for prefix in self._backend_paths
        )

    async def _within_deadline(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        """Give up on a command the sandbox never answers, as the backend's sandbox does."""
        timeout = request.tool_call["args"].get("timeout")
        seconds = timeout if isinstance(timeout, int) else self._execute_timeout
        if request.tool_call["name"] != "execute" or seconds <= 0:
            return await handler(request)
        deadline = seconds + _EXECUTE_GRACE_SECONDS
        try:
            return await asyncio.wait_for(handler(request), deadline)
        except TimeoutError:
            logger.warning("The sandbox never answered a command", extra={"deadline": deadline})
            return ToolMessage(
                content=f"Command timed out after {deadline}s; the sandbox never answered.",
                tool_call_id=request.tool_call.get("id") or "",
                name=request.tool_call["name"],
                status="error",
            )

    async def _errors_as_results(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        """Hand the model what failed, except a box that stopped answering, which ends the run."""
        started = monotonic()
        try:
            return await handler(request)
        except NodeCancelledError:
            raise
        except SandboxRetryableConnectionError as exc:
            logger.warning("The sandbox kept refusing a tool call before it started", exc_info=True)
            return _error_message(
                request,
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "previous_error": str(exc),
                    "recovery": "sandbox_transient",
                    "error": (
                        "The sandbox connection was rejected before this command started, so "
                        "nothing ran and nothing changed."
                    ),
                },
            )
        except Exception as exc:
            if not _sandbox_unreachable(exc):
                logger.exception("Tool call failed", extra={"tool_name": request.tool_call["name"]})
                return _error_message(
                    request,
                    {"error": str(exc), "error_type": type(exc).__name__, "status": "error"},
                )
            # Every later call would meet the same dead box, so the run ends here
            # once the person knows.
            logger.exception("The thread's sandbox stopped answering")
            await self._report_unreachable(request, exc)
            raise
        finally:
            seconds = monotonic() - started
            if seconds > _SLOW_CALL_SECONDS:
                logger.warning(
                    "Slow tool call",
                    extra={
                        "tool_name": request.tool_call["name"],
                        "tool_call_id": request.tool_call.get("id"),
                        "duration_seconds": seconds,
                    },
                )

    async def _report_unreachable(self, request: ToolCallRequest, exc: Exception) -> None:
        sandbox_id = _SANDBOX_ID.search(str(exc))
        body = run_body(
            run_id(), request.state, sandbox_id=sandbox_id.group(0) if sandbox_id else None
        )
        try:
            await self._backend.call("sandbox_unreachable", body)
        except BackendCallError:
            logger.warning("Could not tell the person the sandbox stopped answering", exc_info=True)

    async def _with_agents_md(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        """Append the ``AGENTS.md`` files above a file the model reads, each the first time."""
        result = await handler(request)
        file_path = _absolute(request.tool_call["args"].get("file_path"))
        if (
            request.tool_call["name"] != _READ_FILE
            or file_path is None
            or not isinstance(result, ToolMessage)
            or result.status == "error"
            or not isinstance(result.content, str)
        ):
            return result
        if posixpath.basename(file_path) == _AGENTS_MD:
            self._agents_seen.add(file_path)
            return result
        backend = getattr(request.runtime, "backend", None)
        if not isinstance(backend, BackendProtocol):
            return result
        paths = [path for path in _agents_paths(file_path) if path not in self._agents_seen]
        self._agents_seen.update(paths)
        texts = await asyncio.gather(*(_read_agents_md(backend, path) for path in paths))
        loaded = [(path, text) for path, text in zip(paths, texts, strict=True) if text is not None]
        if not loaded:
            return result
        reminder = _agents_reminder(file_path, loaded)
        return result.model_copy(update={"content": f"{result.content}\n\n{reminder}"})


def _guarded(command: str) -> bool:
    return bool(_GUARDED_WORDS.search(command)) or len(_SHELL.findall(command)) >= _GUARDED_SHELLS


def _absolute(path: object) -> str | None:
    """``path`` as the file tools resolve it, rooted at ``/``."""
    if not isinstance(path, str) or not path.strip():
        return None
    return posixpath.normpath("/" + path.strip().lstrip("/"))


async def _retried(request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
    """Send a call the sandbox refused before it started again, as the backend's sandbox does."""
    for backoff in _TRANSIENT_BACKOFF_SECONDS:
        try:
            return await handler(request)
        except SandboxRetryableConnectionError:
            logger.warning("The sandbox refused a tool call before it started; retrying")
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
    return await handler(request)


def _sandbox_unreachable(exc: Exception) -> bool:
    """Whether the box itself did not answer, as opposed to a call that failed in it.

    A reload left the command running, and a missing resource that is not the box
    is a missing file.
    """
    if isinstance(exc, SandboxConnectionError):
        return not isinstance(exc, SandboxServerReloadError)
    return isinstance(exc, ResourceNotFoundError) and exc.resource_type == "sandbox"


def _error_message(request: ToolCallRequest, payload: dict[str, str]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({**payload, "name": request.tool_call["name"]}),
        tool_call_id=request.tool_call.get("id") or "",
        status="error",
    )


async def _without_false_images(
    request: ToolCallRequest, handler: Handler
) -> ToolMessage | Command:
    """Keep a file with an image's extension but not its bytes from reaching the model as one.

    The provider would reject it, and every later call on the thread with it.
    """
    result = await handler(request)
    if request.tool_call["name"] != _READ_FILE or not isinstance(result, ToolMessage):
        return result
    if not isinstance(result.content, list):
        return result
    for block in result.content:
        head = _image_head(block)
        if head is None or _is_image(head):
            continue
        path = result.additional_kwargs.get("read_file_path", "the file")
        return ToolMessage(
            content=(
                f"read_file: {path} has an image extension but its contents are not a "
                f"valid image (starts with {head[:8]!r}). It was not attached; inspect "
                "it as text or re-download it."
            ),
            name=result.name,
            tool_call_id=result.tool_call_id,
            status="error",
        )
    return result


def _image_head(block: object) -> bytes | None:
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    encoded = block.get("base64")
    if not isinstance(encoded, str):
        return None
    try:
        return base64.b64decode(encoded[:24] + "==")[:12]
    except ValueError:  # binascii.Error included
        return b""


def _is_image(head: bytes) -> bool:
    # HEIC/HEIF and AVIF are ISO-BMFF containers: the `ftyp` box sits at offset 4.
    if head[4:8] == b"ftyp":
        return True
    return any(
        head.startswith(parts[0]) and (len(parts) == 1 or head[8:12] == parts[1])
        for parts in _IMAGE_SIGNATURES
    )


def _agents_paths(file_path: str) -> list[str]:
    """The ``AGENTS.md`` files that apply to ``file_path``, outermost first."""
    paths: list[str] = []
    current = posixpath.dirname(file_path)
    while current not in {"", "/"}:
        paths.append(posixpath.join(current, _AGENTS_MD))
        current = posixpath.dirname(current)
    return list(reversed(paths))


async def _read_agents_md(backend: BackendProtocol, path: str) -> str | None:
    try:
        read = await backend.aread(path, offset=0, limit=_MAX_AGENTS_LINES)
    except Exception:
        logger.debug("Could not read an AGENTS.md", extra={"agents_md_path": path}, exc_info=True)
        return None
    data = read.file_data
    if read.error or data is None:
        return None
    content = data.get("content")
    if data.get("encoding", "utf-8") != "utf-8" or not isinstance(content, str):
        return None
    if not content.strip():
        return None
    encoded = content.encode("utf-8")
    if len(encoded) <= _MAX_AGENTS_BYTES:
        return content
    return encoded[:_MAX_AGENTS_BYTES].decode("utf-8", errors="ignore") + "\n\n[truncated]"


def _agents_reminder(file_path: str, loaded: Iterable[tuple[str, str]]) -> str:
    body = "\n\n".join(f"Instructions from: {path}\n{content.rstrip()}" for path, content in loaded)
    return (
        "<system-reminder>\n"
        f"Loaded applicable AGENTS.md instructions for `{file_path}`. "
        "Follow these before editing files under their scopes; more deeply nested "
        "instructions take precedence.\n\n"
        f"{body}\n"
        "</system-reminder>"
    )
