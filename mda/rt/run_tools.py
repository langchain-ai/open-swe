"""The run's tools and models, from its catalog: offered to each model call, run by name.

MDA builds the graph once and shares it between every run of every thread, so the
graph registers none of the operations a run's catalog names. ``RunToolsMiddleware``
offers each model call the run's own tools and hands LangGraph's tool node the tool a
call names; code mode reaches the same tools (``RunCodeInterpreter``). A subagent's
prompt and model, and the run's limit on model calls, are the run's too.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final
from weakref import finalize

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langchain_quickjs.middleware import PersistenceMode, REPLState
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from runtime_api.catalog import Catalog, SubagentSpec
from runtime_api.loop import ModelCall

from rt.backend import BackendCallError, OpenSweBackend, Run, RunLocal, current_run
from rt.file_tools import FileToolsMiddleware
from rt.models import build_model
from rt.search_tools import search_tools_tool
from rt.tools import operation_tool
from rt.tracing import MIDDLEWARE_TRACE_POLICY

_TASK_TOOL: Final = "task"
_EVAL_TOOL: Final = "eval"

ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]


def subagent_description(name: str) -> str:
    """What the graph declares as subagent ``name``'s description, which each run's replaces."""
    return f"[[open-swe subagent {name}]]"


def subagent_prompt(name: str) -> str:
    """What the graph declares as subagent ``name``'s system prompt, which each run's replaces."""
    return f"[[open-swe subagent prompt {name}]]"


class RunTools:
    """One run's tools and models, as its catalog names them."""

    def __init__(
        self, catalog: Catalog, backend: OpenSweBackend, files: FileToolsMiddleware
    ) -> None:
        self.catalog = catalog
        operations = [
            operation_tool(spec, backend, cites_evidence=catalog.incident_evidence)
            for spec in catalog.operations
        ]
        searched = (
            [search_tools_tool(catalog.operations, catalog.search_tools_description)]
            if catalog.search_tools
            else []
        )
        self.by_name = {tool.name: tool for tool in (*operations, *searched)}
        self.excluded = frozenset(catalog.excluded)
        # Every schema offered costs each call its context, and OpenAI answers some
        # requests carrying hundreds with nothing; the tools the model seldom needs are
        # called from code.
        code_only = frozenset(catalog.code_only)
        self.offered = [tool for tool in self.by_name.values() if tool.name not in code_only]
        in_code = [tool for tool in operations if tool.name in code_only]
        self.from_code = [
            *in_code,
            *(t for t in operations if t.name in catalog.also_from_code and t not in in_code),
            *files.code_tools(catalog.also_from_code),
        ]
        self.hidden = self.excluded | code_only
        self.subagents = {subagent.name: subagent for subagent in catalog.subagents}
        models = catalog.models
        self.subagent_model: BaseChatModel | None = (
            None
            if models is None
            else build_model(models.subagent_model, use_gateway=models.use_gateway)
        )

    def subagent_tools(self, subagent: SubagentSpec) -> list[BaseTool]:
        """What ``subagent`` may call of the run's tools: all it is offered, or those it names."""
        if subagent.tools is None:
            return self.offered
        return [self.by_name[name] for name in subagent.tools if name in self.by_name]


class RunToolsets:
    """Each run's ``RunTools``, made from its catalog once, and again once a worker adopts its own."""

    def __init__(self, backend: OpenSweBackend, files: FileToolsMiddleware) -> None:
        self._backend = backend
        self._files = files
        self._runs = RunLocal[list[RunTools]](list)

    def get(self, run: Run | None = None) -> RunTools:
        run = run if run is not None else self._backend.run
        held = self._runs.get(run)
        if not held or held[0].catalog is not run.catalog:
            held[:] = [RunTools(run.catalog, self._backend, self._files)]
        return held[0]


class RunToolsMiddleware(AgentMiddleware):
    """Offer each model call the run's tools as the graph would, and run each call of one.

    The backend's tools act on its sandbox, so they stay offered even where MDA's
    filesystem middleware drops one it cannot back itself, such as ``execute``. Hidden
    tools still run when called by name, as in-process; the backend names the ones its
    middleware hides from each call. ``subagent`` names the subagent this middleware
    serves; the agent itself otherwise.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, toolsets: RunToolsets, *, subagent: str | None = None) -> None:
        self._toolsets = toolsets
        self._subagent = subagent
        # The ``task`` tool as each run describes its subagents, by the description.
        self._task_tools = RunLocal[dict[tuple[int, str], BaseTool]](dict)

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        tools = self._toolsets.get()
        if self._subagent is None:
            hidden = tools.hidden
            own = tools.offered
        else:
            hidden = tools.excluded
            spec = tools.subagents.get(self._subagent)
            own = [] if spec is None else tools.subagent_tools(spec)
        planned = request.state.get("open_swe_call")
        if isinstance(planned, dict):
            hidden |= frozenset(ModelCall.model_validate(planned).hidden_tools)
        if not tools.subagents:
            hidden |= {_TASK_TOOL}
        offered = [
            self._described(tool, tools)
            if isinstance(tool, BaseTool) and tool.name == _TASK_TOOL
            else tool
            for tool in request.tools
            if getattr(tool, "name", None) not in hidden
        ]
        names = {getattr(tool, "name", None) for tool in offered}
        missing = [tool for tool in own if tool.name not in names | hidden]
        return await handler(request.override(tools=[*offered, *missing]))

    def _described(self, task: BaseTool, tools: RunTools) -> BaseTool:
        """``task`` describing the run's own subagents."""
        description = task.description
        for spec in tools.subagents.values():
            description = description.replace(subagent_description(spec.name), spec.description)
        key = (id(task), description)
        task_tools = self._task_tools.get(current_run())
        if key not in task_tools:
            task_tools[key] = task.model_copy(update={"description": description})
        return task_tools[key]

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolMessage | Command:
        if request.tool is None:
            tool = self._toolsets.get().by_name.get(request.tool_call["name"])
            if tool is not None:
                request = request.override(tool=tool)
        return await handler(request)


class RunSubagentMiddleware(AgentMiddleware):
    """Give subagent ``name`` the system prompt and model the run's catalog gives it."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, toolsets: RunToolsets, name: str) -> None:
        self._toolsets = toolsets
        self._name = name

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        tools = self._toolsets.get()
        spec = tools.subagents.get(self._name)
        if spec is not None and request.system_message is not None:
            text = request.system_message.text.replace(
                subagent_prompt(self._name), spec.system_prompt
            )
            request = request.override(system_message=SystemMessage(content=text))
        if tools.subagent_model is not None:
            request = request.override(model=tools.subagent_model)
        return await handler(request)


class RunCodeInterpreter(CodeInterpreterMiddleware):
    """Code mode over the run's tools: the graph's own it names, and those the run offers from code.

    ``active`` says whether a run has code mode at all; one without it is offered no
    ``eval`` and pays nothing for the interpreter.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(
        self,
        toolsets: RunToolsets,
        *,
        named: Sequence[str | BaseTool] = (),
        active: Callable[[RunTools], bool] = lambda _tools: True,
        subagents: bool = True,
        mode: PersistenceMode | None = None,
    ) -> None:
        self._toolsets = toolsets
        self._named = list(named)
        self._active = active
        self._prompts = RunLocal[list[tuple[frozenset[str], str] | None]](lambda: [None])
        self._slots = RunLocal[set[str]](set)
        super().__init__(ptc=[], subagents=subagents, mode=mode)

    @property
    def _ptc_prompt_cache(self) -> tuple[frozenset[str], str] | None:
        return self._prompts.get(current_run())[0]

    @_ptc_prompt_cache.setter
    def _ptc_prompt_cache(self, value: tuple[frozenset[str], str] | None) -> None:
        if value is not None:
            self._prompts.get(current_run())[0] = value

    def _slot_id(self, state: Mapping[str, object]) -> str:
        slot_id = super()._slot_id(state)
        run = current_run()
        slots = self._slots.get(run)
        if slot_id not in slots:
            slots.add(slot_id)
            finalize(run, self._evict, slot_id)
        return slot_id

    def _evict(self, slot_id: str) -> None:
        self._ptc_tools_by_slot.pop(slot_id, None)
        self._registry.evict(slot_id)

    @property
    def _ptc(self) -> list[str | BaseTool]:
        try:
            tools = self._toolsets.get()
        except BackendCallError, RuntimeError:
            # Outside a run, such as at build: only the graph's own.
            return list(self._named)
        return [*self._named, *tools.from_code]

    @_ptc.setter
    def _ptc(self, _ptc: object) -> None:
        """The interpreter sets its tools once; these are each run's."""

    def _runs_code(self) -> bool:
        return self._active(self._toolsets.get())

    async def abefore_agent(self, state: REPLState, runtime: Runtime) -> dict[str, object] | None:
        if not self._runs_code():
            return None
        update = self._slot_update_for_runtime()
        prepared: REPLState = {**state, "_quickjs_slot_id": update["_quickjs_slot_id"]}
        self._slot_id(prepared)
        restored = await super().abefore_agent(prepared, runtime)
        return {**update, **(restored or {})} or None

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        if not self._runs_code():
            tools = [tool for tool in request.tools if getattr(tool, "name", None) != _EVAL_TOOL]
            return await handler(request.override(tools=tools))
        return await super().awrap_model_call(request, handler)

    async def aafter_agent(self, state: REPLState, runtime: Runtime) -> dict[str, object] | None:
        if not self._runs_code():
            return None
        return await super().aafter_agent(state, runtime)


class RunModelCallLimit(ModelCallLimitMiddleware):
    """End a run at its catalog's limit on model calls, or at ``default`` for a catalog without one."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend, default: int) -> None:
        self._run_backend = backend
        self._default = default
        super().__init__(run_limit=default, exit_behavior="end")

    @property
    def run_limit(self) -> int:
        try:
            limit = self._run_backend.run.catalog.model_call_limit
        except BackendCallError, RuntimeError:
            return self._default
        return self._default if limit is None else limit

    @run_limit.setter
    def run_limit(self, _limit: int | None) -> None:
        """The middleware sets its limit once; this is each run's."""
