"""Run each model call as the Open SWE thread that dispatched it.

Open SWE sends the system prompt, model choice and its tool surface in the run
context. Tools other than the deep agent's own run back on Open SWE through its
sandbox tools endpoint, authenticated by a capability for this thread and the
name of this thread's sandbox.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, NotRequired
from urllib.parse import quote

import httpx
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool, ToolException
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel, JsonValue

TOKEN_HEADER = "X-Open-SWE-MDA-Token"
SANDBOX_HEADER = "X-Open-SWE-MDA-Sandbox"
LOAD_TOOL = "load_integration_tools"
FORKED_KEY = "_deepagents_forked_context"
PATH_ARGS = {"ls": "path", "read_file": "file_path", "glob": "path"}
CATALOG_CACHE_SIZE = 64


class ModelSpec(BaseModel):
    id: str
    kwargs: dict[str, JsonValue]


class OpenSweTools(BaseModel):
    url: str
    token: str
    excluded: list[str]
    subagent_excluded: list[str]
    subagent_unavailable: str
    integrations_description: str | None
    remote_paths: list[str]


class OpenSweContext(BaseModel):
    """Mirrors ``MdaContext`` in Open SWE's ``agent/mda.py``."""

    tools: OpenSweTools
    system_prompt: str
    model: ModelSpec
    subagent_model: ModelSpec
    fallback_model: ModelSpec | None


class RemoteTool(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]
    integration: bool = False


def _merge_names(current: list[str], update: list[str]) -> list[str]:
    return sorted(set(current) | set(update))


class OpenSweState(AgentState):
    loaded_integration_tools: NotRequired[Annotated[list[str], _merge_names]]


_catalogs: dict[str, dict[str, RemoteTool]] = {}
_models: dict[str, BaseChatModel] = {}
_sandbox_names: dict[str, str] = {}


def _context(runtime: object) -> OpenSweContext | None:
    context = getattr(runtime, "context", None)
    if context is None or isinstance(context, OpenSweContext):
        return context
    return OpenSweContext.model_validate(context)


def _model(spec: ModelSpec) -> BaseChatModel:
    key = spec.model_dump_json()
    if key not in _models:
        _models[key] = init_chat_model(spec.id, **spec.kwargs)
    return _models[key]


async def _sandbox_name(runtime: object, thread_id: str) -> str:
    """The managed sandbox's name, which Open SWE uses to run file-reading tools in it."""
    if thread_id not in _sandbox_names:
        backend = getattr(runtime, "backend", None)
        if backend is None:
            raise RuntimeError("Open SWE tools need the managed sandbox")
        await backend.als("/")
        if backend.id.startswith("pending:"):
            raise RuntimeError("The managed sandbox did not resolve")
        _sandbox_names[thread_id] = backend.id
    return _sandbox_names[thread_id]


async def _client(context: OpenSweContext, runtime: object) -> httpx.AsyncClient:
    thread_id = str(get_config()["configurable"]["thread_id"])
    headers = {
        TOKEN_HEADER: context.tools.token,
        SANDBOX_HEADER: await _sandbox_name(runtime, thread_id),
    }
    return httpx.AsyncClient(base_url=context.tools.url, headers=headers, timeout=600)


async def _catalog(context: OpenSweContext, runtime: object) -> dict[str, RemoteTool]:
    if context.tools.token in _catalogs:
        return _catalogs[context.tools.token]
    tools: dict[str, RemoteTool] = {}
    async with await _client(context, runtime) as client:
        while True:
            response = await client.get("/list", params={"offset": len(tools)})
            response.raise_for_status()
            page = response.json()
            tools.update({t["name"]: RemoteTool.model_validate(t) for t in page["tools"]})
            if not page["tools"] or len(tools) >= page["total"]:
                break
    if len(_catalogs) >= CATALOG_CACHE_SIZE:
        _catalogs.pop(next(iter(_catalogs)))
    _catalogs[context.tools.token] = tools
    return tools


async def _invoke(
    context: OpenSweContext, runtime: object, name: str, arguments: dict[str, object]
) -> JsonValue:
    async with await _client(context, runtime) as client:
        response = await client.post(f"/invoke/{quote(name, safe='')}", json=arguments)
    if response.is_error:
        raise ToolException(f"{name} failed ({response.status_code}): {response.text}")
    result = response.json()
    if result["status"] == "error":
        raise ToolException(json.dumps(result["content"]))
    return result["content"]


def _remote_tool(context: OpenSweContext, runtime: object, spec: RemoteTool) -> BaseTool:
    properties = spec.parameters.get("properties")
    names = set(properties) if isinstance(properties, dict) else set()

    async def run(**arguments: object) -> JsonValue:
        sent = {key: value for key, value in arguments.items() if key in names}
        return await _invoke(context, runtime, spec.name, sent)

    return StructuredTool(
        name=spec.name,
        description=spec.description,
        args_schema=spec.parameters,
        coroutine=run,
        handle_tool_error=True,
    )


def _load_tool(description: str, available: set[str]) -> BaseTool:
    async def load_integration_tools(
        tool_names: list[str], tool_call_id: Annotated[str, InjectedToolCallId]
    ) -> Command:
        unknown = sorted(set(tool_names) - available)
        if unknown:
            message = f"Unknown integration tools: {', '.join(unknown)}"
            return Command(
                update={
                    "messages": [ToolMessage(message, tool_call_id=tool_call_id, status="error")]
                }
            )
        loaded = ", ".join(sorted(tool_names))
        message = f"Loaded integration tool schemas: {loaded}. Call these tools normally on your next turn."
        return Command(
            update={
                "loaded_integration_tools": tool_names,
                "messages": [ToolMessage(message, tool_call_id=tool_call_id)],
            }
        )

    return StructuredTool.from_function(
        coroutine=load_integration_tools, name=LOAD_TOOL, description=description
    )


def _remote_tools(
    context: OpenSweContext, runtime: object, catalog: dict[str, RemoteTool]
) -> dict[str, BaseTool]:
    tools = {name: _remote_tool(context, runtime, spec) for name, spec in catalog.items()}
    integrations = {spec.name for spec in catalog.values() if spec.integration}
    if integrations and context.tools.integrations_description:
        tools[LOAD_TOOL] = _load_tool(context.tools.integrations_description, integrations)
    return tools


def _model_visible(name: str, catalog: dict[str, RemoteTool], loaded: list[str]) -> bool:
    spec = catalog.get(name)
    if spec is None:
        return True
    return name not in PATH_ARGS and (not spec.integration or name in loaded)


def _tool_name(tool: BaseTool | dict[str, object]) -> object:
    return tool.name if isinstance(tool, BaseTool) else tool.get("name")


def _forked(state: object) -> bool:
    return isinstance(state, dict) and bool(state.get(FORKED_KEY))


def _excluded(context: OpenSweContext, state: object) -> set[str]:
    excluded = set(context.tools.excluded)
    return excluded | set(context.tools.subagent_excluded) if _forked(state) else excluded


def _routed_path(context: OpenSweContext, request: ToolCallRequest) -> bool:
    arg = PATH_ARGS.get(request.tool_call["name"])
    path = request.tool_call["args"].get(arg) if arg else None
    return isinstance(path, str) and path.startswith(tuple(context.tools.remote_paths))


class OpenSweMiddleware(AgentMiddleware[OpenSweState]):
    state_schema = OpenSweState

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = _context(request.runtime)
        if context is None:
            return await handler(request)
        catalog = await _catalog(context, request.runtime)
        excluded = _excluded(context, request.state)
        loaded = request.state.get("loaded_integration_tools") or []
        remote = _remote_tools(context, request.runtime, catalog)
        tools = [
            *request.tools,
            *(tool for name, tool in remote.items() if _model_visible(name, catalog, loaded)),
        ]
        existing = request.system_message.text if request.system_message else ""
        spec = context.subagent_model if _forked(request.state) else context.model
        request = request.override(
            model=_model(spec),
            system_message=SystemMessage(f"{context.system_prompt}\n\n{existing}".strip()),
            tools=[tool for tool in tools if _tool_name(tool) not in excluded],
        )
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception:
            if context.fallback_model is None or _forked(request.state):
                raise
            return await handler(request.override(model=_model(context.fallback_model)))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        context = _context(request.runtime)
        if context is None:
            return await handler(request)
        call = request.tool_call
        if _forked(request.state) and call["name"] in context.tools.subagent_excluded:
            message = context.tools.subagent_unavailable
            return ToolMessage(message, tool_call_id=call["id"], status="error")
        remote = _remote_tools(context, request.runtime, await _catalog(context, request.runtime))
        tool = remote.get(call["name"])
        if tool is None or (call["name"] in PATH_ARGS and not _routed_path(context, request)):
            return await handler(request)
        return await handler(request.override(tool=tool))
