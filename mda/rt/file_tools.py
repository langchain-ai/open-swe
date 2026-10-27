"""The run's file and shell tools, with the checks around them.

They run in the thread's box, which MDA owns, or against the thread's files on the
backend (``rt.remote_files``): where the box is not MDA's, such as a thread bridged
to a person's machine, and on the paths the backend serves. Reads of the run's
skills are answered from the skills this runtime keeps (``rt.skills``).

Around every call: a command that may push or open a pull request is first put to
the backend's guards, which inspect the push and may need a person's approval, so
the match here is wider than what they block. Tool errors become results the model
can act on. Where the catalog lists the checks, a file posing as an image is not
attached and reading a file loads the ``AGENTS.md`` files above it. The transcript
hears of each call alongside it. Code mode calls them through ``code_tools``, so
its calls meet the same checks.
"""

import asyncio
import json
import logging
import posixpath
import random
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import partial
from typing import Final

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith.sandbox import SandboxRetryableConnectionError
from pydantic import JsonValue
from runtime_api.catalog import FilesSpec
from runtime_api.endpoint import call
from runtime_api.operations import CHECK_SHELL, ShellCheck

from rt.backend import OpenSweBackend, RunLocal
from rt.middleware import reported
from rt.remote_files import FILE_TOOLS, RemoteFiles, remote_file_tools
from rt.subdir_agents import SubdirAgentsReadMiddleware
from rt.tool_errors import ToolErrorMiddleware
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY
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
    """Run the file and shell tools where the thread's files are, with their checks.

    Where they are, and which checks apply, is the run's: the graph is shared by every
    run, so each call reads them from the run in progress.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(
        self,
        backend: OpenSweBackend,
        execute_timeout: int,
        *,
        remote_files: RemoteFiles,
        skills: Mapping[str, BaseTool] | None = None,
    ) -> None:
        self._backend = backend
        self._remote_files = remote_files
        # Each run's view of the backend's files as deepagents' own tools, with large
        # results under the run's artifacts root, which a bridged thread has its own of.
        self._remote_by_run = RunLocal(
            lambda: remote_file_tools(
                self._remote_files, self._backend.run.catalog.files.artifacts_root
            )
        )
        # The run's skills, which this runtime keeps itself (``rt.skills``).
        self._skills = dict(skills or {})
        # The box's own tools, as the model is offered them; code mode runs them from here.
        self._box: dict[str, BaseTool] = {}
        self._execute_timeout = execute_timeout
        self._tool_errors = ToolErrorMiddleware(backend)
        self._image_reads = ValidateImageReadsMiddleware()
        self._agents_md_in_box = SubdirAgentsReadMiddleware(None)
        self._agents_md_remote = SubdirAgentsReadMiddleware(remote_files)

    @property
    def _in_box(self) -> bool:
        """Whether the run's files are in MDA's box, rather than only on the backend."""
        return self._backend.run.planned.sandbox is not None

    @property
    def _spec(self) -> FilesSpec:
        return self._backend.run.catalog.files

    @property
    def _remote(self) -> dict[str, BaseTool]:
        return self._remote_by_run.get(self._backend.run)

    def code_tools(self, names: Iterable[str]) -> list[StructuredTool]:
        """The file and shell tools among ``names``, for code mode, each call checked."""
        templates = remote_file_tools(self._remote_files, "/")
        return [self._code_tool(templates[name]) for name in sorted({*names} & {*templates})]

    def _code_tool(self, template: BaseTool) -> StructuredTool:
        name = template.name

        async def run(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
            call_ = ToolCall(name=name, args=arguments, id=runtime.tool_call_id, type="tool_call")
            request = ToolCallRequest(
                tool_call=call_, tool=template, state=runtime.state, runtime=runtime
            )
            return await self.awrap_tool_call(request, self._run_own)

        return StructuredTool.from_function(
            coroutine=run,
            name=name,
            description=template.description,
            args_schema=convert_to_openai_tool(template)["function"]["parameters"],
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._box.update(
            (tool.name, tool)
            for tool in request.tools
            if isinstance(tool, BaseTool) and tool.name in FILE_TOOLS
        )
        return await handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        call_ = request.tool_call
        if call_["name"] not in FILE_TOOLS:
            return await handler(request)
        spec = self._spec
        if _under(self._backend.run.catalog.skill_sources, call_["name"], call_["args"]):
            run: Handler = self._run_skill
        elif not self._in_box or _under(spec.backend_paths, call_["name"], call_["args"]):
            run = self._run_remote
        else:
            run = partial(_retried, handler=partial(self._within_deadline, handler=handler))
        # The transcript records a call the guards refuse, with their reason, as any other.
        return await reported(
            self._backend,
            request,
            partial(self._checked, handler=partial(self._screened_run, handler=run)),
        )

    async def _screened_run(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        screened = await self._screened(request)
        if isinstance(screened, ToolMessage):
            return screened
        return await handler(screened)

    async def _screened(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """``request`` as the guards let it run, an approved push as they fixed it, or their refusal."""
        command = request.tool_call["args"].get("command")
        if (
            request.tool_call["name"] != "execute"
            or "shell_commands" not in self._spec.guards
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
        return await _invoked(self._remote[request.tool_call["name"]], request)

    async def _run_skill(self, request: ToolCallRequest) -> ToolMessage | Command:
        """The call against the run's skills, as the box's own tool would run it."""
        tool = self._skills.get(request.tool_call["name"])
        if tool is None:
            return ToolMessage(
                content="Skills are read-only here",
                tool_call_id=request.tool_call.get("id") or "",
                name=request.tool_call["name"],
                status="error",
            )
        return await _invoked(tool, request)

    async def _run_own(self, request: ToolCallRequest) -> ToolMessage | Command:
        """A code mode call, run by the tool the model's own call of it runs."""
        tools = self._box if self._in_box else self._remote
        return await _invoked(tools[request.tool_call["name"]], request)

    async def _checked(self, request: ToolCallRequest, handler: Handler) -> ToolMessage | Command:
        """``handler`` in the graph's own order: image reads, then tool errors, then AGENTS.md."""
        checks = self._spec.checks
        agents_md = self._agents_md_in_box if self._in_box else self._agents_md_remote
        checked = handler
        for check in (
            agents_md if "agents_md" in checks else None,
            self._tool_errors,
            self._image_reads if "image_reads" in checks else None,
        ):
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


async def _invoked(tool: BaseTool, request: ToolCallRequest) -> ToolMessage | Command:
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


def _under(prefixes: Iterable[str], name: str, arguments: Mapping[str, object]) -> bool:
    """Whether the path ``name``'s call acts on is under one of ``prefixes``."""
    path = _absolute(arguments.get(_PATH_ARGUMENTS.get(name, "")))
    return path is not None and any(
        path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes
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
