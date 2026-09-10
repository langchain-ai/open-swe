"""Assembly of a coding agent graph.

The builder owns the platform-agnostic half of the stack: model construction,
the generic tool list, the middleware order, and the general-purpose subagent.
Platform wiring arrives through constructor seams — a backend, extra tools,
integration groups, and four middleware splice slots.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, cast

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.graph import DeepAgentState
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.pregel import Pregel

from coding_agent.config import ENV
from coding_agent.middleware import (
    DynamicToolMiddleware,
    ExcludeToolsMiddleware,
    IntegrationGroup,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    ModelFallbackMiddleware,
    ModelSelectionMiddleware,
    PlanModeMiddleware,
    SandboxFailureNotifier,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    StableToolResultOrderMiddleware,
    SubdirAgentsReadMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
    task_on_failure,
    task_retry_on,
)
from coding_agent.plans import PlanStoreFactory
from coding_agent.prompts import apply_tool_descriptions, load_prompt
from coding_agent.runtime.constants import MODEL_CALL_RECURSION_LIMIT
from coding_agent.tools import (
    background_tools,
    create_sandbox_file_download_url,
    create_sandbox_service_url,
    fetch_url,
    http_request,
    output_iframe,
    plan_tools,
    web_search,
)
from coding_agent.tools.background_execute import BackgroundTaskMonitor
from coding_agent.utils import ttl_cache
from coding_agent.utils.model import ModelChoice, fallback_model_id_for

logger = logging.getLogger(__name__)

# Emitted by `ModelCallLimitMiddleware` when the run budget is exhausted.
MODEL_CALL_LIMIT_MARKER = "Model call limits exceeded"

DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS = 5.0

DEEP_AGENT_TOOL_NAMES = {
    "delete",
    "edit_file",
    "execute",
    "glob",
    "grep",
    "ls",
    "read_file",
    "task",
    "write_file",
}
DEEP_AGENT_EXCLUDED_TOOLS = frozenset({"grep"})

# Generic mutating tools hidden from the model while plan mode is active so it
# can only research and propose a plan. File edit tools stay available so the
# agent can draft and revise a plan under `/workspace/plans/`; prompt guidance
# restricts them to that plan file outside cloned repositories. `execute` stays
# available; plan-mode shell discipline (no mutating commands) is instructed via
# the system prompt rather than enforced. `http_request` is excluded because it
# can POST/PUT/PATCH/DELETE to external services — read-only web research goes
# through `web_search` / `fetch_url`. `task` is excluded because the
# general-purpose subagent is built with its own tools and does not inherit this
# exclusion, so delegating to it would bypass the read-only intent.
PLAN_MODE_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "task",
        "background_execute",
        "background_task",
        "create_sandbox_service_url",
        "http_request",
    }
)

type CoreToolSet = Literal["full", "web", "none"]
type OuterMiddlewareFactory = Callable[[BaseChatModel], Sequence[AgentMiddleware[Any, Any, Any]]]

# Signed-URL tools: generic, but only usable on a sandbox provider that serves
# them, so the caller adds them to its own tool list rather than the builder
# guessing.
SANDBOX_URL_TOOLS: tuple[Any, ...] = (
    output_iframe,
    create_sandbox_file_download_url,
    create_sandbox_service_url,
)


def registered_tool_name(value: Any) -> str:
    name = getattr(value, "name", None) or getattr(value, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"tool has no registered name: {value!r}")
    return name


def tool_loader_timeout_seconds() -> float:
    raw_timeout = ENV.TOOL_LOADER_TIMEOUT_SECONDS.optional()
    if not raw_timeout:
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError:
        logger.warning("Invalid TOOL_LOADER_TIMEOUT_SECONDS=%r; using default", raw_timeout)
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    if timeout <= 0:
        logger.warning("TOOL_LOADER_TIMEOUT_SECONDS must be positive; using default")
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    return timeout


async def cached_tool_loader(key: str, ttl_seconds: float, loader: Any) -> list[Any]:
    """Load integration tools behind a TTL cache; never fail the graph factory."""

    async def load_with_timeout() -> list[Any]:
        return await asyncio.wait_for(loader(), timeout=tool_loader_timeout_seconds())

    try:
        return await ttl_cache.cached_stale_while_revalidate(key, ttl_seconds, load_with_timeout)
    except TimeoutError:
        logger.warning("Timed out loading cached tools for %s", key, exc_info=True)
        return []
    except Exception:
        logger.warning("Failed to load cached tools for %s", key, exc_info=True)
        return []


def _subagent_model_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Provider guards for subagent model calls.

    Subagents compile into their own graphs, so parent middleware never wraps them.
    """
    return cast(
        list[AgentMiddleware[Any, Any, Any]],
        [
            SanitizeOpenAIResponsesMiddleware(),
            ModelErrorMiddleware(),
            ModelCallTimeoutMiddleware(),
        ],
    )


class CodingAgentBuilder:
    """Builds the coding agent graph for one thread.

    ``core_tools`` selects the generic tool list: ``"full"`` for every generic
    tool, ``"web"`` for read-only web research only, ``"none"`` for platform
    tools alone. ``outer_middleware`` is a factory because the outermost
    middleware typically needs the thread-title model, which this builder owns.
    ``middleware`` is spliced before ``TimeoutWrapupMiddleware`` and
    ``late_middleware`` after it.
    """

    def __init__(
        self,
        *,
        model: ModelChoice,
        backend: BackendProtocol,
        subagent_model: ModelChoice | None = None,
        title_model: ModelChoice | None = None,
        routing_models: Mapping[str, ModelChoice] | None = None,
        skill_routes: Mapping[str, BackendProtocol] = MappingProxyType({}),
        skill_sources: Sequence[str] = (),
        state_schema: type[DeepAgentState] | None = None,
        core_tools: CoreToolSet = "full",
        extra_tools: Sequence[Any] = (),
        excluded_tools: frozenset[str] = DEEP_AGENT_EXCLUDED_TOOLS,
        plan_mode_excluded_tools: frozenset[str] = frozenset(),
        integration_tool_groups: Mapping[str, IntegrationGroup | Sequence[Any]] = MappingProxyType(
            {}
        ),
        subagent_tool_filter: Callable[[Any], bool] | None = None,
        subagent_prompt_sections: Sequence[str] = (),
        outer_middleware: OuterMiddlewareFactory | None = None,
        middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
        late_middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
        subagent_middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
        plan_mode: bool = False,
        plans: PlanStoreFactory | None = None,
        sandbox_failure_notifier: SandboxFailureNotifier | None = None,
        background_tasks: BackgroundTaskMonitor | None = None,
    ) -> None:
        self._model = model
        self._subagent_model = subagent_model or model
        self._title_model = title_model or model
        self._backend = backend
        self._routing_models = routing_models
        self._skill_routes = skill_routes
        self._skill_sources = list(skill_sources)
        self._state_schema = state_schema
        self._core_tool_set = core_tools
        self._extra_tools = extra_tools
        self._excluded_tools = excluded_tools
        self._plan_mode_excluded_tools = plan_mode_excluded_tools
        self._integration_tool_groups = integration_tool_groups
        self._subagent_tool_filter = subagent_tool_filter
        self._subagent_prompt_sections = subagent_prompt_sections
        self._outer_middleware = outer_middleware
        self._middleware = middleware
        self._late_middleware = late_middleware
        self._subagent_middleware = subagent_middleware
        self._plan_mode = plan_mode
        self._plans = plans
        self._sandbox_failure_notifier = sandbox_failure_notifier
        self._background_tasks = background_tasks

    def _fallback_middleware(self) -> list[Any]:
        model_id = self._model.model_id
        fallback_model_id = ENV.LLM_FALLBACK_MODEL_ID.optional() or fallback_model_id_for(model_id)
        if not fallback_model_id or fallback_model_id == model_id:
            return []
        fallback = ModelChoice(fallback_model_id, use_gateway=self._model.use_gateway)
        logger.info("Configured model fallback %s -> %s", model_id, fallback_model_id)
        return [ModelFallbackMiddleware(fallback.chat_model())]

    def _core_tools(self, background_execute: Any, background_task: Any) -> list[Any]:
        if self._core_tool_set == "none":
            return []
        tools: list[Any] = [http_request, fetch_url, web_search]
        if self._core_tool_set == "web":
            return tools
        tools.extend([background_execute, background_task])
        if self._plans is not None:
            tools.extend(plan_tools(self._plans))
        return tools

    def _dynamic_tool_middleware(self, tools: Sequence[Any]) -> DynamicToolMiddleware | None:
        if not self._integration_tool_groups:
            return None
        candidate = DynamicToolMiddleware(
            dict(self._integration_tool_groups),
            reserved_names={
                *DEEP_AGENT_TOOL_NAMES,
                *(registered_tool_name(tool) for tool in tools),
            },
        )
        return candidate if candidate.has_groups else None

    def _build_subagent_middleware(
        self, dynamic_tools: DynamicToolMiddleware | None
    ) -> list[AgentMiddleware[Any, Any, Any]]:
        middleware: list[AgentMiddleware[Any, Any, Any]] = []
        if dynamic_tools is not None:
            middleware.append(dynamic_tools)
        middleware.append(ExcludeToolsMiddleware(excluded=DEEP_AGENT_EXCLUDED_TOOLS))
        middleware.extend(self._subagent_middleware)
        middleware.extend(_subagent_model_middleware())
        return middleware

    def _general_purpose_subagent(
        self,
        model: BaseChatModel,
        tools: Sequence[Any],
        dynamic_tools: DynamicToolMiddleware | None,
    ) -> SubAgent:
        excluded = self._subagent_tool_filter or (lambda _tool: False)
        # Deep Agents' default GP prompt covers only task mechanics; the injected
        # sections carry the platform identity and conventions that delegated
        # work also needs.
        prompt_sections = [
            *self._subagent_prompt_sections,
            GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        ]
        subagent: SubAgent = {
            "name": GENERAL_PURPOSE_SUBAGENT["name"],
            "description": (
                f"{GENERAL_PURPOSE_SUBAGENT['description']} "
                f"{load_prompt('system/general-purpose-subagent-suffix.md')}"
            ),
            "system_prompt": "\n\n".join(prompt_sections),
            "model": model,
            "tools": [tool for tool in tools if not excluded(tool)],
            "middleware": self._build_subagent_middleware(dynamic_tools),
        }
        if self._skill_sources:
            subagent["skills"] = list(self._skill_sources)
        return subagent

    async def build(self, thread_id: str) -> Pregel:
        """Compile the agent graph for ``thread_id``."""
        logger.info("Building coding agent for thread %s", thread_id)
        fallback_middleware = self._fallback_middleware()

        background_execute, background_task = background_tools(self._background_tasks)
        tools = apply_tool_descriptions(
            [*self._core_tools(background_execute, background_task), *self._extra_tools]
        )
        dynamic_tool_middleware = self._dynamic_tool_middleware(tools)

        main_model = self._model.chat_model()
        model_selection_middleware: list[Any] = []
        if self._routing_models is not None:
            routed = {route: choice.chat_model() for route, choice in self._routing_models.items()}
            model_selection_middleware.append(
                ModelSelectionMiddleware(routed, routed["fast"], initial_plan_mode=self._plan_mode)
            )
        subagent_model = self._subagent_model.chat_model()
        subagent_tools = [
            tool for tool in tools if tool is not background_execute and tool is not background_task
        ]
        title_model = self._title_model.chat_model()

        return create_deep_agent(
            model=main_model,
            system_prompt="",
            tools=tools,
            subagents=[
                self._general_purpose_subagent(
                    subagent_model, subagent_tools, dynamic_tool_middleware
                ),
            ],
            skills=list(self._skill_sources),
            backend=CompositeBackend(default=self._backend, routes=dict(self._skill_routes)),
            state_schema=self._state_schema,
            middleware=cast(
                list[AgentMiddleware[Any, Any, Any]],
                [
                    *(self._outer_middleware(title_model) if self._outer_middleware else []),
                    *([dynamic_tool_middleware] if dynamic_tool_middleware else []),
                    SanitizeToolInputsMiddleware(),
                    ModelCallLimitMiddleware(
                        run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"
                    ),
                    ToolErrorMiddleware(notifier=self._sandbox_failure_notifier),
                    ExcludeToolsMiddleware(excluded=self._excluded_tools),
                    SubdirAgentsReadMiddleware(),
                    ToolRetryMiddleware(
                        max_retries=2,
                        tools=["task"],
                        retry_on=task_retry_on,
                        on_failure=task_on_failure,
                        initial_delay=1.0,
                        max_delay=10.0,
                    ),
                    *self._middleware,
                    TimeoutWrapupMiddleware(),
                    *self._late_middleware,
                    *model_selection_middleware,
                    *fallback_middleware,
                    PlanModeMiddleware(
                        excluded=PLAN_MODE_EXCLUDED_TOOLS | self._plan_mode_excluded_tools,
                        initial=self._plan_mode,
                    ),
                    SanitizeFireworksMessagesMiddleware(),
                    SanitizeOpenAIResponsesMiddleware(),
                    SanitizeThinkingBlocksMiddleware(),
                    StableToolResultOrderMiddleware(),
                    ModelErrorMiddleware(),
                    # Innermost, so the deadline covers the provider call itself and a
                    # timeout escalates outward to the fallback model.
                    ModelCallTimeoutMiddleware(),
                ],
            ),
        )
