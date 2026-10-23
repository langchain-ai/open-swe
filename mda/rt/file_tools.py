"""The run's file and shell tools, with the checks around them.

They run in the thread's box, which MDA owns, or against the thread's files on the
backend (``rt.remote_files``): where the box is not MDA's, such as a thread bridged
to a person's machine, and on the paths the backend serves, such as skills.

Around every call: a command that may push or open a pull request is first put to
the backend's guards, which inspect the push and may need a person's approval, so
the match here is wider than what they block. Tool errors become results the model
can act on. Where the catalog lists the checks, a file posing as an image is not
attached and reading a file loads the ``AGENTS.md`` files above it. The transcript
hears of each call alongside it. Code mode's calls skip the checks, as code mode
always has.
"""

import asyncio
import json
import logging
import posixpath
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import SandboxRetryableConnectionError
from runtime_api.catalog import FilesSpec
from runtime_api.endpoint import call
from runtime_api.operations import CHECK_SHELL, ShellCheck

from rt.backend import OpenSweBackend
from rt.middleware import reported
from rt.remote_files import FILE_TOOLS
from rt.subdir_agents import SubdirAgentsReadMiddleware
from rt.tool_errors import ToolErrorMiddleware
from rt.tools import run_id
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


class FileToolsMiddleware(AgentMiddleware):
    """Run the file and shell tools where the thread's files are, with their checks."""

    def __init__(
        self,
        backend: OpenSweBackend,
        spec: FilesSpec,
        execute_timeout: int,
        *,
        remote: Mapping[str, BaseTool],
        remote_files: BackendProtocol,
        in_box: bool,
    ) -> None:
        self._backend = backend
        self._remote = dict(remote)
        self._in_box = in_box
        self._backend_paths = tuple(spec.backend_paths)
        self._guards_shell = "shell_commands" in spec.guards
        self._execute_timeout = execute_timeout
        self._tool_errors = ToolErrorMiddleware(backend)
        self._image_reads = ValidateImageReadsMiddleware() if "image_reads" in spec.checks else None
        self._agents_md = (
            SubdirAgentsReadMiddleware(None if in_box else remote_files)
            if "agents_md" in spec.checks
            else None
        )

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        call_ = request.tool_call
        if call_["name"] not in FILE_TOOLS:
            return await handler(request)
        screened = await self._screened(request)
        if isinstance(screened, ToolMessage):
            return screened
        request = screened
        if self._in_box and self._on_backend(call_["name"], call_["args"]):
            run: Handler = self._run_remote
        else:
            run = partial(_retried, handler=partial(self._within_deadline, handler=handler))
        return await reported(self._backend, request, partial(self._checked, handler=run))

    def _on_backend(self, name: str, arguments: Mapping[str, object]) -> bool:
        path = _absolute(arguments.get(_PATH_ARGUMENTS.get(name, "")))
        return path is not None and any(
            path == prefix.rstrip("/") or path.startswith(prefix) for prefix in self._backend_paths
        )

    async def _screened(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """``request`` as the guards let it run, an approved push as they fixed it, or their refusal."""
        command = request.tool_call["args"].get("command")
        if (
            request.tool_call["name"] != "execute"
            or not self._guards_shell
            or not isinstance(command, str)
            or not _guarded(command)
        ):
            return request
        verdict = await call(
            self._backend, CHECK_SHELL, ShellCheck(run_id=run_id(), tool="execute", command=command)
        )
        if not verdict.allowed:
            return ToolMessage(
                content=verdict.refusal or json.dumps({"status": "error", "error": "refused"}),
                tool_call_id=request.tool_call.get("id") or "",
                name="execute",
                status="error",
            )
        if verdict.command is None:
            return request
        arguments = {**request.tool_call["args"], "command": verdict.command}
        return request.override(tool_call={**request.tool_call, "args": arguments})

    async def _run_remote(self, request: ToolCallRequest) -> ToolMessage | Command:
        """The call against the backend's files, as the box's own tool would run it."""
        tool = self._remote[request.tool_call["name"]]
        call_id = request.tool_call.get("id") or ""
        result = await tool.ainvoke(
            ToolCall(
                name=tool.name,
                args={**request.tool_call["args"], "runtime": request.runtime},
                id=call_id,
                type="tool_call",
            )
        )
        if isinstance(result, ToolMessage | Command):
            return result
        return ToolMessage(content=str(result), tool_call_id=call_id, name=tool.name)

    async def _checked(self, request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
        """``handler`` in the graph's own order: image reads, then tool errors, then AGENTS.md."""
        checked = handler
        for check in (self._agents_md, self._tool_errors, self._image_reads):
            if check is not None:
                checked = partial(check.awrap_tool_call, handler=checked)
        return await checked(request)

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
