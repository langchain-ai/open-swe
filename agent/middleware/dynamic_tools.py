"""Defer integration tools with LangChain's supported middleware."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from langchain.agents.middleware import LLMToolSelectorMiddleware, ProviderToolSearchMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)

_OPENAI_TOOL_SEARCH_VERSION = re.compile(r"^gpt-(?P<major>\d+)(?:\.(?P<minor>\d+))?")
_ANTHROPIC_TOOL_SEARCH_MODEL = re.compile(
    r"^claude-(?:sonnet-(?:[4-9](?:-|$))|opus-(?:[4-9](?:-|$))|haiku-(?:4-5(?:-|$)|[5-9](?:-|$)))"
)
_OPENAI_DIRECT_BASES = {"https://api.openai.com/v1", "wss://api.openai.com/v1"}
_ANTHROPIC_DIRECT_BASE = "https://api.anthropic.com"


@dataclass(frozen=True)
class IntegrationGroup:
    """A connected integration with its catalog and per-run tool resolver."""

    tool_names: Sequence[str]
    load: Callable[[], Awaitable[Sequence[BaseTool]]]


class DynamicToolMiddleware(OpenSWEMiddleware):
    """Resolve integration tools once, then defer or select their schemas."""

    def __init__(
        self,
        groups: Mapping[str, IntegrationGroup | Sequence[BaseTool]],
        reserved_names: Collection[str] = (),
    ) -> None:
        self._groups: dict[str, IntegrationGroup] = {}
        self._group_of: dict[str, str] = {}
        self._tools: dict[str, BaseTool] | None = None
        self._resolve_lock = asyncio.Lock()
        self._selector = LLMToolSelectorMiddleware(on_parsing_failure="all")

        for group, spec in groups.items():
            entry = spec if isinstance(spec, IntegrationGroup) else _eager_group(spec)
            names = list(entry.tool_names)
            for name in names:
                if name in reserved_names or name in self._group_of:
                    raise ValueError(f"Duplicate integration tool name: {name}")
                self._group_of[name] = group
            if names:
                self._groups[group] = entry

    @property
    def has_groups(self) -> bool:
        return bool(self._groups)

    async def _resolve_tools(self) -> dict[str, BaseTool]:
        if self._tools is not None:
            return self._tools
        async with self._resolve_lock:
            if self._tools is not None:
                return self._tools
            groups = sorted(self._groups)
            loaded = await asyncio.gather(*(self._load_group(group) for group in groups))
            tools: dict[str, BaseTool] = {}
            for group, group_tools in zip(groups, loaded, strict=True):
                for tool in group_tools:
                    if self._group_of.get(tool.name) != group or tool.name in tools:
                        raise ValueError(f"Duplicate integration tool name: {tool.name}")
                    tools[tool.name] = tool
            self._tools = tools
        return self._tools

    async def _load_group(self, group: str) -> Sequence[BaseTool]:
        try:
            return await self._groups[group].load()
        except Exception:
            logger.warning(
                "Failed to load integration tools",
                extra={"integration_group": group},
                exc_info=True,
            )
            return []

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        integration_tools = list((await self._resolve_tools()).values())
        if not integration_tools:
            return await handler(request)

        integration_request = request.override(tools=[*integration_tools])
        if _supports_provider_tool_search(request.model):
            middleware = ProviderToolSearchMiddleware(
                searchable_tools=[tool.name for tool in integration_tools]
            )
            return await middleware.awrap_model_call(
                request.override(tools=[*request.tools, *integration_tools]), handler
            )

        async def recombine(selected_request: ModelRequest) -> ModelResponse:
            return await handler(
                selected_request.override(
                    tools=[*request.tools, *selected_request.tools],
                    messages=request.messages,
                )
            )

        if not any(isinstance(message, HumanMessage) for message in request.messages):
            return await recombine(integration_request)
        return await self._selector.awrap_model_call(integration_request, recombine)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        if name not in self._group_of:
            return await handler(request)
        tool = (await self._resolve_tools()).get(name)
        if tool is None:
            return ToolMessage(
                content=f"{name} is unavailable right now. Continue without it.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request.override(tool=tool))


def _supports_provider_tool_search(model: object) -> bool:
    """Return whether a model is a supported direct-provider model."""
    if isinstance(model, ChatAnthropic):
        return (
            str(model.anthropic_api_url).rstrip("/") == _ANTHROPIC_DIRECT_BASE
            and _ANTHROPIC_TOOL_SEARCH_MODEL.match(model.model) is not None
        )
    if not isinstance(model, ChatOpenAI):
        return False
    base_url = str(model.openai_api_base or "https://api.openai.com/v1").rstrip("/")
    match = _OPENAI_TOOL_SEARCH_VERSION.match(model.model_name)
    if match is None or base_url not in _OPENAI_DIRECT_BASES:
        return False
    version = (int(match.group("major")), int(match.group("minor") or 0))
    return version >= (5, 5)


def _eager_group(tools: Sequence[BaseTool]) -> IntegrationGroup:
    """Wrap tools that are already resolved."""

    async def load() -> Sequence[BaseTool]:
        return tools

    return IntegrationGroup(tool_names=[tool.name for tool in tools], load=load)
