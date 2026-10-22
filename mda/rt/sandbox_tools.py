"""The file and shell tools of the thread's box, run here with the graph's own checks.

Where MDA owns the thread's box, MDA's own file and shell tools serve the graph's
against it, with no call to the backend in between. Tool errors become results the
model can act on, where the backend's catalog lists that check for the graph, and
for the coding agent a file posing as an image is not attached and reading a file
loads the ``AGENTS.md`` files above it. The transcript hears of each call alongside it.

The backend still runs the calls only it can settle: calls on the paths it serves,
such as skills, and, where the graph guards pushes and pull requests, commands that
may push or open one. Those guards inspect the push and may need a person's
approval, so the match here is wider than what they block: nothing they would stop
runs in the box. The checks on what ``read_file`` returns apply to those calls too;
code mode's calls skip them, as code mode always has.
"""

import asyncio
import logging
import posixpath
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import Final

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import SandboxRetryableConnectionError

from rt.backend import OpenSweBackend
from rt.middleware import reported
from rt.sandbox import SandboxSpec
from rt.subdir_agents import SubdirAgentsReadMiddleware
from rt.tool_errors import ToolErrorMiddleware
from rt.tools import run_on_backend
from rt.validate_image_reads import ValidateImageReadsMiddleware

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
# The guards read a command as the shell splits it, with its quotes and escapes gone,
# and the shell joins a line it continues.
_QUOTING: Final = re.compile(r"\\\n|[\"'\\]")
# As the backend's sandbox: the client gives up on a command the sandbox never
# answers 30 seconds past its own timeout.
_EXECUTE_GRACE_SECONDS: Final = 30
# A sandbox call rejected before it started cannot have run, so it is sent again.
_TRANSIENT_BACKOFF_SECONDS: Final = (0.5, 1.0, 2.0)

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


class SandboxToolsMiddleware(AgentMiddleware):
    """Run the box's file and shell tools here, and the calls only the backend can settle there."""

    def __init__(
        self,
        backend: OpenSweBackend,
        spec: SandboxSpec,
        execute_timeout: int,
        *,
        check_reads: bool,
    ) -> None:
        self._backend = backend
        self._tools = frozenset(spec.tools)
        self._guards_pushes = "push_guards" in spec.checks
        self._backend_paths = tuple(spec.backend_paths)
        self._execute_timeout = execute_timeout
        self._image_reads = ValidateImageReadsMiddleware() if check_reads else None
        self._tool_errors = ToolErrorMiddleware(backend) if "tool_errors" in spec.checks else None
        self._agents_md = SubdirAgentsReadMiddleware() if check_reads else None

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        call = request.tool_call
        if call["name"] not in self._tools:
            return await handler(request)
        if self._on_backend(call["name"], call["args"]):
            # The backend turns its own errors into results and records the call.
            return await self._checked_answer(request, await self._run_on_backend(request))
        in_box = partial(_retried, handler=partial(self._within_deadline, handler=handler))
        return await reported(
            self._backend,
            request,
            partial(self._checked, handler=in_box, errors=self._tool_errors),
        )

    def _on_backend(self, name: str, arguments: Mapping[str, object]) -> bool:
        if name == "execute":
            command = arguments.get("command")
            return self._guards_pushes and isinstance(command, str) and _guarded(command)
        path = _absolute(arguments.get(_PATH_ARGUMENTS.get(name, "")))
        return path is not None and any(
            path == prefix.rstrip("/") or path.startswith(prefix) for prefix in self._backend_paths
        )

    async def _checked(
        self, request: ToolCallRequest, handler: Handler, errors: ToolErrorMiddleware | None
    ) -> ToolMessage | Command:
        """``handler`` in the graph's own order: image reads, then tool errors, then AGENTS.md."""
        checked = handler
        for check in (self._agents_md, errors, self._image_reads):
            if check is not None:
                checked = partial(check.awrap_tool_call, handler=checked)
        return await checked(request)

    async def _checked_answer(self, request: ToolCallRequest, command: Command) -> Command:
        """The read checks on the tool message a backend call answered with, inside its command."""
        update = command.update if isinstance(command.update, dict) else {}
        messages = update.get("messages")
        call_id = request.tool_call.get("id")
        answer = next(
            (
                message
                for message in (messages if isinstance(messages, list) else [])
                if isinstance(message, ToolMessage) and message.tool_call_id == call_id
            ),
            None,
        )
        if answer is None or not isinstance(messages, list):
            return command

        async def answered(_request: ToolCallRequest) -> ToolMessage:
            return answer

        checked = await self._checked(request, answered, errors=None)
        if checked is answer or not isinstance(checked, ToolMessage):
            return command
        return replace(
            command,
            update={**update, "messages": [checked if m is answer else m for m in messages]},
        )

    async def _run_on_backend(self, request: ToolCallRequest) -> Command:
        call = request.tool_call
        return await run_on_backend(self._backend, call["name"], call["args"], request.runtime)

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


def _guarded(command: str) -> bool:
    unquoted = _QUOTING.sub("", command)
    return bool(_GUARDED_WORDS.search(unquoted)) or len(_SHELL.findall(unquoted)) >= _GUARDED_SHELLS


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
