"""Hold the agent to the directory it opened: editing needs one, and paths and commands start there."""

import json
import posixpath
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Annotated, NotRequired, cast

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware.types import AgentState
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel, Field, create_model

from openswe.middleware.pr_creation_guard import SHELL_TOOL_ARGS
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.sandboxes.context_directory import CONTEXT_DIRECTORY_KEY, ContextDirectory
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.state import get_sandbox_backend
from openswe.utils.json_types import JsonObject

OPEN_DIRECTORY = "open_directory"
# Each file tool's path argument. Search tools' are optional and default to the open directory.
_PATH_ARGUMENTS = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "glob": "path",
    "grep": "path",
}
_EDIT_TOOLS = frozenset({"write_file", "edit_file", "delete"})


def _replace(left: JsonObject | None, right: JsonObject | None) -> JsonObject | None:
    """Unlike a plain channel, lets ``None`` close the directory and parallel calls both write."""
    del left
    return right


class ContextDirectoryState(AgentState):
    context_directory: NotRequired[Annotated[JsonObject | None, _replace]]


async def open_directory(
    path: Annotated[
        str,
        Field(
            description="The directory to work in: absolute, or relative to the open directory "
            "(the sandbox work directory when none is open)."
        ),
    ],
    runtime: ToolRuntime[None, ContextDirectoryState],
) -> Command:
    thread_id = RunConfig.from_config(runtime.config).thread_id
    if not thread_id:
        raise ValueError("No active thread")
    sandbox = await get_sandbox_backend(thread_id)
    current = ContextDirectory.of(runtime.state)
    base = current.path if current else await resolve_sandbox_work_dir(sandbox)
    directory = await ContextDirectory.open(sandbox, posixpath.join(base, path))
    await directory.record(thread_id)
    recorded = directory.model_dump()
    return Command(
        update={
            CONTEXT_DIRECTORY_KEY: recorded,
            "messages": [
                ToolMessage(content=json.dumps(recorded), tool_call_id=runtime.tool_call_id or "")
            ],
        }
    )


class ContextDirectoryMiddleware(OpenSWEMiddleware[ContextDirectoryState]):
    """Refuse file edits until a directory is open, then run the agent's sandbox calls from it.

    Commands run from the open directory and relative paths resolve from it. Guards
    that read commands as the model wrote them go outside this one and run their own
    checks from the open directory; those that read file paths go inside it.
    """

    state_schema = ContextDirectoryState

    def __init__(self, filesystem: FilesystemMiddleware) -> None:
        filesystem.tools = [_with_relative_path(tool) for tool in filesystem.tools]
        self.tools: list[BaseTool] = [
            StructuredTool.from_function(
                coroutine=open_directory,
                name=OPEN_DIRECTORY,
                description=prompt("tools/open_directory"),
            )
        ]

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        args = dict(request.tool_call["args"])
        directory = ContextDirectory.of(request.state)
        argument = _PATH_ARGUMENTS.get(name)
        path = args.get(argument) if argument else None
        relative = isinstance(path, str) and bool(path) and not path.startswith(("/", "~"))
        if directory is None:
            if name in _EDIT_TOOLS or relative:
                return _refusal(request, relative=name not in _EDIT_TOOLS)
            return await handler(request)
        commands = {
            key: value
            for key in SHELL_TOOL_ARGS.get(name, ())
            if isinstance(value := args.get(key), str) and value
        }
        if argument is not None and (relative or path is None):
            args[argument] = directory.resolve(path) if isinstance(path, str) else directory.path
        elif commands:
            args.update({key: directory.command(value) for key, value in commands.items()})
        else:
            return await handler(request)
        return await handler(request.override(tool_call={**request.tool_call, "args": args}))


def _refusal(request: ToolCallRequest, *, relative: bool) -> ToolMessage:
    return ToolMessage(
        content=prompt("tools/open-directory-required", relative=relative),
        tool_call_id=request.tool_call["id"] or "",
        name=request.tool_call["name"],
        status="error",
    )


def _with_relative_path(tool: BaseTool) -> BaseTool:
    """``tool`` with its path argument described as resolving from the open directory."""
    argument = _PATH_ARGUMENTS.get(tool.name)
    schema = tool.args_schema
    if argument is None or not isinstance(schema, type) or not issubclass(schema, BaseModel):
        return tool
    return tool.model_copy(update={"args_schema": _relative_path_schema(schema, argument)})


@cache
def _relative_path_schema(schema: type[BaseModel], argument: str) -> type[BaseModel]:
    # Cached because graphs are built on every run.
    field = schema.model_fields[argument]
    description = prompt("tools/relative-path", optional=not field.is_required())
    # Field names are only known at runtime, which create_model's overloads cannot express.
    derive = cast(Callable[..., type[BaseModel]], create_model)
    return derive(
        schema.__name__,
        __base__=schema,
        **{argument: (field.annotation, Field(default=field.default, description=description))},
    )
