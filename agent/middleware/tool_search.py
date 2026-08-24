"""Put the tool catalog behind a search tool and an invoke tool.

The model is shown two tools whose schemas never change, so the tool array it
receives is byte-identical on every call of every run. Provider prompt caches
key on a prefix that begins with that array, so anything which grows it mid-run
discards the whole cached prompt — measured at over 100k tokens on a single
call. Everything else is reachable through `tool_search`, which returns
schemas as ordinary tool output at the end of the transcript, where new bytes
cost only themselves.

The proxy exists only at the model boundary. A `tool_invoke` call is rewritten
into a normal tool call before it reaches state, so the tool node, the
transcript, and every downstream consumer see the real tool and never learn
this middleware is here. The reverse rewrite collapses those calls again on the
way out, keeping the wire self-consistent for providers that validate history
against the declared tools.
"""

import asyncio
import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command

logger = logging.getLogger(__name__)

TOOL_SEARCH_NAME = "tool_search"
TOOL_DESCRIBE_NAME = "tool_describe"
TOOL_INVOKE_NAME = "tool_invoke"
_DEFAULT_LIMIT = 8
_SUMMARY_CHARS = 200
_MAX_LIMIT = 25


def _merge_names(current: list[str], update: list[str]) -> list[str]:
    return sorted(set(current) | set(update))


class ToolSearchState(AgentState):
    searched_tools: NotRequired[Annotated[list[str], _merge_names]]


@dataclass(frozen=True)
class ToolGroup:
    """A set of tools described by name before anything is built.

    ``load`` runs at most once, the first time one of the group's tools is
    searched or invoked. Groups whose tools are already built pass them through
    ``from_tools`` and resolve instantly.
    """

    tool_names: Sequence[str]
    load: Callable[[], Awaitable[Sequence[BaseTool]]]
    summary: str = ""
    prebuilt: Sequence[BaseTool] | None = None

    @classmethod
    def from_tools(cls, tools: Sequence[Any], summary: str = "") -> "ToolGroup":
        """A group that is already built, so its descriptions are searchable at once."""

        built = [_as_base_tool(tool) for tool in tools]

        async def load() -> Sequence[BaseTool]:
            return built

        return cls(
            tool_names=[tool.name for tool in built],
            load=load,
            summary=summary,
            prebuilt=built,
        )


def _as_base_tool(value: Any) -> BaseTool:
    """Accept the callables the curated tool list mixes in with `BaseTool`s."""
    if isinstance(value, BaseTool):
        return value
    if asyncio.iscoroutinefunction(value):
        return StructuredTool.from_function(coroutine=value)
    return StructuredTool.from_function(value)


@dataclass
class _Resolved:
    tools: dict[str, BaseTool] = field(default_factory=dict)
    done: bool = False


def _schema_of(tool: BaseTool) -> dict[str, Any]:
    """The tool's parameters, without what the rendered description already says.

    Pydantic repeats the whole docstring in `description` and titles every field
    after itself; both are pure duplication once the description is rendered
    above the schema, and they were 44% of a search result.
    """
    try:
        schema = cast(Any, tool.tool_call_schema).model_json_schema()
    except Exception:  # noqa: BLE001
        return {}
    for key in ("title", "description"):
        schema.pop(key, None)
    for prop in (schema.get("properties") or {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


def _render(tool: BaseTool) -> str:
    """Everything needed to call the tool."""
    schema = _schema_of(tool)
    parameters = json.dumps(schema, sort_keys=True) if schema else "{}"
    return f"### {tool.name}\n{tool.description.strip()}\n\nparameters: {parameters}"


def _summarize(tool: BaseTool) -> str:
    """Enough to choose between candidates, and no more.

    A full render of every match is most of a search result's cost while the
    model is still deciding which tool it wants.
    """
    paragraph = " ".join((tool.description or "").strip().split("\n\n")[0].split())
    if len(paragraph) > _SUMMARY_CHARS:
        paragraph = paragraph[:_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return f"- {tool.name}: {paragraph}" if paragraph else f"- {tool.name}"


class ToolSearchMiddleware(AgentMiddleware[ToolSearchState]):
    """Show the model two stable tools and route everything else through them."""

    state_schema = ToolSearchState

    def __init__(
        self,
        groups: Mapping[str, ToolGroup | Sequence[BaseTool]],
        *,
        always_visible: Collection[str] = (),
    ) -> None:
        self._always_visible = {
            *always_visible,
            TOOL_SEARCH_NAME,
            TOOL_DESCRIBE_NAME,
            TOOL_INVOKE_NAME,
        }
        self._groups: dict[str, ToolGroup] = {}
        self._group_of: dict[str, str] = {}
        self._resolved: dict[str, _Resolved] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._loading: dict[str, asyncio.Task[Any]] = {}

        for group, spec in groups.items():
            entry = spec if isinstance(spec, ToolGroup) else ToolGroup.from_tools(spec)
            names = [name for name in entry.tool_names if name not in self._always_visible]
            # A group that cannot name its tools without a remote call still
            # registers: loading discovers them, and `_register` adds them then.
            if not names and entry.prebuilt is not None:
                continue
            self._groups[group] = entry
            self._register(group, names)
            if entry.prebuilt is not None:
                # Already built, so full-text search can read their descriptions
                # without any remote call.
                self._resolved[group] = _Resolved(
                    tools={tool.name: tool for tool in entry.prebuilt}, done=True
                )

        self.tools = [self._search_tool(), self._describe_tool(), self._invoke_tool()]

    # ---- catalog -------------------------------------------------------

    def _catalog(self) -> str:
        lines = []
        for group, entry in sorted(self._groups.items()):
            names = ", ".join(sorted(n for n in entry.tool_names if n in self._group_of))
            summary = f" — {entry.summary}" if entry.summary else ""
            lines.append(f"- {group}{summary}: {names}")
        return "\n".join(lines)

    @property
    def proxied_names(self) -> frozenset[str]:
        return frozenset(self._group_of)

    # ---- resolution ----------------------------------------------------

    async def _resolve(self, group: str) -> dict[str, BaseTool]:
        resolved = self._resolved.setdefault(group, _Resolved())
        if resolved.done:
            return resolved.tools
        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            if resolved.done:
                return resolved.tools
            try:
                tools = await self._groups[group].load()
            except Exception:
                logger.warning("Failed to load tool group %s", group, exc_info=True)
                tools = []
            resolved.tools = {tool.name: tool for tool in tools}
            resolved.done = True
            self._register(group, resolved.tools)
        return resolved.tools

    def _register(self, group: str, names: Iterable[str]) -> None:
        for name in names:
            if name not in self._always_visible:
                self._group_of[name] = group

    def start_loading(self) -> None:
        """Begin building every unbuilt group, without waiting for any of it.

        Nothing on the critical path awaits these. A search matches whatever has
        landed by the time it runs, which — since loading starts a model call or
        more earlier — is normally everything, and is how a query reaches a tool
        whose name it does not already know.
        """
        for group in self._groups:
            if (self._resolved.get(group) or _Resolved()).done:
                continue
            task = self._loading.get(group)
            if task is not None and not task.done():
                continue
            self._loading[group] = asyncio.create_task(self._resolve(group))

    async def _tool(self, name: str) -> BaseTool | None:
        group = self._group_of.get(name)
        if group is None:
            return None
        return (await self._resolve(group)).get(name)

    # ---- the two model-visible tools -----------------------------------

    def _search_tool(self) -> BaseTool:
        async def tool_search(
            query: str,
            limit: int = _DEFAULT_LIMIT,
            tool_call_id: Annotated[str, InjectedToolCallId] = "",  # noqa: ARG001
        ) -> Command:
            matches = await self._search(query, limit)
            if not matches:
                body = (
                    f"No tools matched {query!r}. The catalog is:\n{self._catalog()}"
                    if self._catalog()
                    else f"No tools matched {query!r}."
                )
            elif len(matches) == 1:
                # Nothing left to choose between, so skip the describe round trip.
                body = _render(matches[0])
            else:
                body = "\n".join(_summarize(tool) for tool in matches) + (
                    f"\n\nCall `{TOOL_DESCRIBE_NAME}` with the names you want before "
                    f"`{TOOL_INVOKE_NAME}`."
                )
            return Command(
                update={
                    "searched_tools": [tool.name for tool in matches],
                    "messages": [
                        ToolMessage(content=body, tool_call_id=tool_call_id, name=TOOL_SEARCH_NAME)
                    ],
                }
            )

        return StructuredTool.from_function(
            coroutine=tool_search,
            name=TOOL_SEARCH_NAME,
            description=(
                "Find the tools available for a task. Matches on tool names and descriptions. "
                "A single match comes back in full and is ready to run; several come back as "
                f"one line each, so follow up with `{TOOL_DESCRIBE_NAME}` for the ones you want."
                "\nAvailable groups:\n" + self._catalog()
            ),
        )

    def _describe_tool(self) -> BaseTool:
        async def tool_describe(
            names: list[str],
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> Command:
            found: list[BaseTool] = []
            missing: list[str] = []
            for name in names:
                tool = await self._tool(name)
                if tool is None:
                    missing.append(name)
                else:
                    found.append(tool)
            sections = [_render(tool) for tool in found]
            if missing:
                sections.append(
                    f"Not available: {', '.join(sorted(missing))}. "
                    f"Use `{TOOL_SEARCH_NAME}` to find the right name."
                )
            return Command(
                update={
                    "searched_tools": [tool.name for tool in found],
                    "messages": [
                        ToolMessage(
                            content="\n\n".join(sections) or "No names given.",
                            tool_call_id=tool_call_id,
                            name=TOOL_DESCRIBE_NAME,
                            status="error" if missing and not found else "success",
                        )
                    ],
                }
            )

        return StructuredTool.from_function(
            coroutine=tool_describe,
            name=TOOL_DESCRIBE_NAME,
            description=(
                "Read the full description and parameters of tools found with "
                f"`{TOOL_SEARCH_NAME}`. Pass every name you are considering at once rather than "
                "one per turn."
            ),
        )

    def _invoke_tool(self) -> BaseTool:
        async def tool_invoke(
            name: str,
            arguments: dict[str, Any] | None = None,  # noqa: ARG001
            state: Annotated[ToolSearchState | None, InjectedState] = None,  # noqa: ARG001
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> ToolMessage:
            # Reached only when the response rewrite did not run, which means the
            # named tool is not one this middleware can route.
            return ToolMessage(
                content=(
                    f"No tool named {name!r} is available. Use {TOOL_SEARCH_NAME} to find one."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        return StructuredTool.from_function(
            coroutine=tool_invoke,
            name=TOOL_INVOKE_NAME,
            description=(
                "Run a tool found with "
                f"`{TOOL_SEARCH_NAME}`. Pass its exact name and an arguments object matching the "
                "parameters that search returned."
            ),
        )

    async def _search(self, query: str, limit: int) -> list[BaseTool]:
        """Full-text match over the name and description of every built tool.

        A group still loading is simply absent from this result rather than
        something to wait on; it will be there for the next search.
        """
        terms = [term for term in query.lower().replace(",", " ").split() if term]
        bounded = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
        if not terms:
            return []
        self.start_loading()

        scored: list[tuple[int, str, BaseTool]] = []
        for group in sorted(self._groups):
            for name, tool in (self._resolved.get(group) or _Resolved()).tools.items():
                if name not in self._group_of:
                    continue
                score = self._score(terms, name, tool.description or "")
                if score:
                    scored.append((-score, name, tool))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [tool for _, _, tool in scored[:bounded]]

    @staticmethod
    def _score(terms: Sequence[str], name: str, description: str) -> int:
        name_l = name.lower()
        desc_l = description.lower()
        score = 0
        for term in terms:
            if term in name_l:
                score += 4
            if term in desc_l:
                score += 1
        return score

    # ---- the model boundary --------------------------------------------

    def _collapse(self, messages: Sequence[AnyMessage]) -> list[AnyMessage]:
        """Rewrite historical calls to proxied tools into `tool_invoke` form."""
        collapsed: list[AnyMessage] = []
        renamed: set[str] = set()
        for message in messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                calls = []
                changed = False
                for call in message.tool_calls:
                    if call.get("name") in self._group_of:
                        changed = True
                        renamed.add(str(call.get("id")))
                        calls.append(
                            {
                                **call,
                                "name": TOOL_INVOKE_NAME,
                                "args": {
                                    "name": call.get("name"),
                                    "arguments": call.get("args") or {},
                                },
                            }
                        )
                    else:
                        calls.append(call)
                collapsed.append(
                    message.model_copy(update={"tool_calls": calls}) if changed else message
                )
                continue
            if isinstance(message, ToolMessage) and message.tool_call_id in renamed:
                collapsed.append(message.model_copy(update={"name": TOOL_INVOKE_NAME}))
                continue
            collapsed.append(message)
        return collapsed

    def _expand(self, response: ModelResponse) -> ModelResponse:
        """Rewrite `tool_invoke` calls back into the tool the model meant."""
        result = getattr(response, "result", None)
        if not isinstance(result, list):
            return response
        rewritten: list[Any] = []
        changed = False
        for message in result:
            if not isinstance(message, AIMessage) or not message.tool_calls:
                rewritten.append(message)
                continue
            calls = []
            for call in message.tool_calls:
                target = self._invoked_name(call)
                if target is None:
                    calls.append(call)
                    continue
                changed = True
                args = (call.get("args") or {}).get("arguments")
                calls.append(
                    {**call, "name": target, "args": args if isinstance(args, dict) else {}}
                )
            rewritten.append(message.model_copy(update={"tool_calls": calls}) if calls else message)
        if not changed:
            return response
        return dataclasses.replace(response, result=rewritten)

    def _invoked_name(self, call: Mapping[str, Any]) -> str | None:
        if call.get("name") != TOOL_INVOKE_NAME:
            return None
        target = (call.get("args") or {}).get("name")
        if isinstance(target, str) and target in self._group_of:
            return target
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        visible = [
            tool for tool in request.tools if getattr(tool, "name", None) not in self._group_of
        ]
        response = await handler(
            request.override(tools=visible, messages=self._collapse(request.messages))
        )
        return self._expand(response)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool_call["name"]
        # The agent registers the curated tools itself; only groups it never saw
        # need this middleware to supply the implementation.
        if name not in self._group_of or request.tool is not None:
            return await handler(request)
        tool = await self._tool(name)
        if tool is None:
            return ToolMessage(
                content=f"{name} is unavailable right now. Continue without it.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request.override(tool=tool))

    async def abefore_agent(self, state: ToolSearchState, runtime: Runtime) -> None:  # noqa: ARG002
        """Start building the tool catalog while the first model call runs."""
        self.start_loading()
        return None

    async def abefore_model(self, state: ToolSearchState, runtime: Runtime) -> None:  # noqa: ARG002
        """Rebuild the groups a resumed run already searched."""
        searched = state.get("searched_tools") or []
        groups = {self._group_of[name] for name in searched if name in self._group_of}
        if groups:
            await asyncio.gather(*(self._resolve(group) for group in sorted(groups)))
        return None
