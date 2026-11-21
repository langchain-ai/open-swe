"""The run's connected integrations, whose tools are built only when a run's catalog needs them."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from openswe.utils.startup_trace import apm_span

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntegrationGroup:
    """A connected integration, described by name and built on request.

    Building the tools is what costs — an MCP handshake, a credential round
    trip — so it waits until the run's catalog asks for them.
    """

    tool_names: Sequence[str]
    load: Callable[[], Awaitable[Sequence[BaseTool]]]


@dataclass
class _Resolved:
    tools: dict[str, BaseTool] = field(default_factory=dict)
    done: bool = False


class DynamicToolMiddleware:
    """The connected integrations' tools, each group built once and on request."""

    def __init__(
        self,
        groups: Mapping[str, IntegrationGroup | Sequence[BaseTool]],
        reserved_names: Collection[str] = (),
    ) -> None:
        self._groups: dict[str, IntegrationGroup] = {}
        self._group_of: dict[str, str] = {}
        self._resolved: dict[str, _Resolved] = {}
        self._locks: dict[str, asyncio.Lock] = {}

        for group, spec in groups.items():
            entry = spec if isinstance(spec, IntegrationGroup) else _eager_group(spec)
            names: list[str] = []
            for name in entry.tool_names:
                if name in reserved_names or name in self._group_of:
                    raise ValueError(f"Duplicate integration tool name: {name}")
                self._group_of[name] = group
                names.append(name)
            if names:
                self._groups[group] = entry

    @property
    def has_groups(self) -> bool:
        return bool(self._groups)

    async def catalog_tools(self) -> list[BaseTool]:
        """Resolve every connected tool, for a runtime that calls them from code."""
        await asyncio.gather(*(self._resolve(group) for group in sorted(self._groups)))
        return [tool for name in self._group_of if (tool := self._tool(name)) is not None]

    async def _resolve(self, group: str) -> dict[str, BaseTool]:
        resolved = self._resolved.setdefault(group, _Resolved())
        if resolved.done:
            return resolved.tools
        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            if resolved.done:
                return resolved.tools
            try:
                with apm_span("start_run.catalog.integration", {"integration": group}):
                    tools = await self._groups[group].load()
            except Exception:
                logger.warning("Failed to load %s integration tools", group, exc_info=True)
                tools = []
            resolved.tools = {tool.name: tool for tool in tools}
            resolved.done = True
        return resolved.tools

    def _tool(self, name: str) -> BaseTool | None:
        group = self._group_of.get(name)
        if group is None:
            return None
        return self._resolved.get(group, _Resolved()).tools.get(name)


def _eager_group(tools: Sequence[BaseTool]) -> IntegrationGroup:
    """Wrap tools that are already built, so both forms share one code path."""

    async def load() -> Sequence[BaseTool]:
        return tools

    return IntegrationGroup(tool_names=[tool.name for tool in tools], load=load)
