"""Run the agent loop on the Managed Deep Agents deployment built from ``mda/``.

Open SWE stays the control plane: the thread, run preparation, the system prompt,
reply and usage bookkeeping, and every tool run here. The MDA deployment runs the
model loop in its own sandbox and calls those tools back through the sandbox
tools endpoint with a thread-scoped capability.
"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Annotated, Any, NotRequired, cast

from deepagents.graph import DeepAgentState
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain.agents.middleware.types import OmitFromOutput
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, convert_to_messages
from langgraph.config import get_config
from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel.remote import RemoteGraph
from langgraph.types import Command
from pydantic import BaseModel

from agent.config import ENV
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.desktop import is_desktop_run
from agent.prompts import load_prompt
from agent.run_config import RunConfig
from agent.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from agent.sandboxes.tool_access import issue_mda_tool_access, tools_endpoint_configured
from agent.utils.model import ModelKwargs, fallback_model_id_for, provider_model_kwargs

MDA_ASSISTANT_ID = "open-swe"
MDA_WORK_DIR = "/workspace"
# Tools the MDA agent has natively, or that need a sandbox runner MDA does not provide.
MDA_SURFACE_EXCLUDED_TOOLS = frozenset(
    {
        "background_execute",
        "background_task",
        "delete",
        "edit_file",
        "execute",
        "recreate_sandbox",
        "task",
        "write_file",
    }
)


class ModelSpec(BaseModel):
    id: str
    kwargs: ModelKwargs


class MdaTools(BaseModel):
    """The thread's tool surface, which the MDA agent calls back into."""

    url: str
    token: str
    excluded: list[str]
    subagent_excluded: list[str]
    subagent_unavailable: str
    integrations_description: str | None
    remote_paths: list[str]


class MdaContext(BaseModel):
    """The run context the MDA agent receives; mirrors ``mda/middleware/open_swe.py``."""

    tools: MdaTools
    system_prompt: str
    model: ModelSpec
    subagent_model: ModelSpec
    fallback_model: ModelSpec | None


async def mda_agent_enabled(cfg: RunConfig) -> bool:
    if is_desktop_run(cfg) or cfg.source == "incidents_agent" or cfg.stop_summary:
        return False
    if cfg.client_tools or not ENV.MDA_AGENT_URL.optional() or not tools_endpoint_configured():
        return False
    return (await cached_workspace_settings(cfg.workspace_slug)).mda_agent_enabled


def model_spec(model_id: str, effort: str | None) -> ModelSpec:
    kwargs = provider_model_kwargs(model_id, effort, max_tokens=DEFAULT_LLM_MAX_TOKENS)
    return ModelSpec(id=model_id, kwargs=kwargs)


class MdaSyncState(AgentState):
    mda_synced: NotRequired[Annotated[bool, OmitFromOutput]]


def _unsynced(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Messages added on this side since the remote thread last answered."""
    for index in range(len(messages) - 1, -1, -1):
        if not isinstance(messages[index], HumanMessage):
            return list(messages[index + 1 :])
    return list(messages)


class MdaDelegateMiddleware(AgentMiddleware[MdaSyncState]):
    """Answer each model call with a full MDA run on the same thread ID.

    The first call sends the whole history, so a thread that ran here before
    arrives with its context; later calls send only what was added since.
    """

    state_schema = MdaSyncState

    def __init__(
        self, tools: MdaTools, default_model: tuple[str, str | None], subagent_model: ModelSpec
    ) -> None:
        super().__init__()
        self._tools = tools
        self._default_model = default_model
        self._subagent_model = subagent_model
        self._remote = RemoteGraph(
            MDA_ASSISTANT_ID,
            url=ENV.MDA_AGENT_URL.get(),
            api_key=ENV.MDA_AGENT_API_KEY.optional(),
            distributed_tracing=True,
        )

    def _context(self, request: ModelRequest) -> MdaContext:
        model_id = request.state.get("selected_model_id")
        model_id, effort = (
            (model_id, request.state.get("selected_effort"))
            if isinstance(model_id, str)
            else self._default_model
        )
        fallback_id = ENV.LLM_FALLBACK_MODEL_ID.optional() or fallback_model_id_for(model_id)
        return MdaContext(
            tools=self._tools,
            system_prompt=request.system_message.text if request.system_message else "",
            model=model_spec(model_id, effort),
            subagent_model=self._subagent_model,
            fallback_model=model_spec(fallback_id, None) if fallback_id else None,
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        del handler
        synced = request.state.get("mda_synced")
        result = await self._remote.ainvoke(
            {"messages": _unsynced(request.messages) if synced else request.messages},
            get_config(),
            context=self._context(request).model_dump(mode="json"),
            multitask_strategy="interrupt",
            on_disconnect="cancel",
        )
        known = {message.id for message in request.messages}
        messages = [m for m in convert_to_messages(result["messages"]) if m.id not in known]
        return ExtendedModelResponse(
            model_response=ModelResponse(result=messages),
            command=Command(update={"mda_synced": True}),
        )


def build_mda_agent(
    *,
    thread_id: str,
    model: BaseChatModel,
    middleware: Sequence[AgentMiddleware[Any, Any, Any]],
    default_model: tuple[str, str | None],
    subagent_model: tuple[str, str | None],
    excluded_tools: Collection[str],
    subagent_excluded_tools: Collection[str],
    integration_tools_description: str | None,
    remote_paths: Sequence[str],
) -> CompiledStateGraph:
    """Open SWE's run middleware around a model node that delegates to MDA."""
    access = issue_mda_tool_access(thread_id)
    if access is None:
        raise RuntimeError("The MDA agent needs the sandbox tools endpoint configured")
    tools = MdaTools(
        url=access[0],
        token=access[1],
        excluded=sorted(excluded_tools),
        subagent_excluded=sorted(subagent_excluded_tools),
        subagent_unavailable=load_prompt("tools/subagent-unavailable.md"),
        integrations_description=integration_tools_description,
        remote_paths=list(remote_paths),
    )
    delegate = MdaDelegateMiddleware(tools, default_model, model_spec(*subagent_model))
    stack = cast(list[AgentMiddleware[Any, Any, Any]], [*middleware, delegate])
    return create_agent(model=model, middleware=stack, state_schema=DeepAgentState)
