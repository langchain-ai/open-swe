"""``define_deep_agent`` authoring contract.

Mirrors the TypeScript ``defineDeepAgent`` surface: accept the full
``create_deep_agent`` keyword surface minus managed keys, typed with the same
LangChain / deepagents types the underlying harness uses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Never, NotRequired, TypedDict

from managed_deepagents._managed_composition import (
    OMITTED,
    GeneratedSkill,
    McpSelection,
    Omitted,
    SkillSelection,
    copy_managed_selections,
)

if TYPE_CHECKING:
    from deepagents import (
        AsyncSubAgent,
        CompiledSubAgent,
        FilesystemPermission,
        SubAgent,
    )
    from langchain.agents.middleware import InterruptOnConfig
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain.agents.structured_output import ResponseFormat
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.cache.base import BaseCache

    from managed_deepagents.define_browser import BrowserDefinition
    from managed_deepagents.define_memory import MemoryDefinition
    from managed_deepagents.sandbox import SandboxDefinition

    #: Same tool union accepted by ``create_deep_agent`` / ``SubAgent``.
    DeepAgentTool = BaseTool | Callable[..., Any] | dict[str, Any]
    # `async` is a keyword, so the flag needs the functional TypedDict form.
    _AsyncFlag = TypedDict("_AsyncFlag", {"async": bool})

    class ManagedAsyncSubAgent(SubAgent, _AsyncFlag):
        """A declarative subagent that MDA builds as its own graph."""

        update_strategy: NotRequired[Literal["interrupt", "enqueue"]]
        """Whether follow-ups interrupt the active run (default) or queue behind it."""

        webhook: NotRequired[str]
        """URL the Agent Server calls whenever a run of this subagent finishes."""

        notify_parent: NotRequired[bool]
        """Whether this subagent may wake this agent when it finishes while this agent is idle.

        Defaults to `True`.

        Set it to `False` when the application delivers results itself, for example
        through `webhook`. This agent still sees the subagent's updates while it runs.

        !!! warning "Beta"

            `notify_parent` may change along with how subagents wake their parent.
        """

    #: Declarative, compiled, or async subagent specs from deepagents, plus
    #: declarative specs marked `"async": True`.
    DeepAgentSubAgent = SubAgent | ManagedAsyncSubAgent | CompiledSubAgent | AsyncSubAgent
    DeepAgentMiddleware = AgentMiddleware
    DeepAgentInterruptOn = bool | InterruptOnConfig
    DeepAgentResponseFormat = ResponseFormat[Any] | type[Any] | dict[str, Any]
    DeepAgentModel = str | BaseChatModel


@dataclass(frozen=True)
class DeepAgentDefinition:
    """The pre-runtime spec returned by :func:`define_deep_agent`.

    It is intentionally *not* a compiled graph: the CLI generates an entry
    module that hands this definition to the managed runtime, which injects the
    managed backend/store/checkpointer and compiles it into a Deep Agent.
    """

    config: dict[str, object]
    kind: ClassVar[str] = "deep-agent"

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        keys = ", ".join(sorted(self.config))
        return f"DeepAgentDefinition({keys})"


DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS: dict[str, str] = {
    "backend": "Configure the sandbox in sandbox/.",
    "store": "MDA provides the store automatically.",
    "checkpointer": "MDA provides the checkpointer automatically.",
    "system_prompt": (
        "Use `instructions` in a factory entrypoint or put the static prompt in instructions.md."
    ),
    "state_schema": "Custom state schemas are not supported in Python.",
}


def define_deep_agent(
    *,
    name: str,
    model: DeepAgentModel | None = None,
    tools: Sequence[DeepAgentTool] | None = None,
    middleware: Sequence[DeepAgentMiddleware] = (),
    subagents: Sequence[DeepAgentSubAgent] | None = None,
    permissions: Sequence[FilesystemPermission] | None = None,
    interrupt_on: Mapping[str, DeepAgentInterruptOn] | None = None,
    response_format: DeepAgentResponseFormat | None = None,
    context_schema: type[Any] | None = None,
    debug: bool = False,
    cache: BaseCache | None = None,
    instructions: str | Omitted | None = OMITTED,
    mcp: McpSelection | Omitted | None = OMITTED,
    sandbox: SandboxDefinition | Omitted | None = OMITTED,
    skills: SkillSelection | Omitted | None = OMITTED,
    memory: MemoryDefinition | Omitted | None = OMITTED,
    browser: BrowserDefinition | Omitted | None = OMITTED,
    disable_memory: bool | None = None,
    metadata: Mapping[str, object] | None = None,
    replicate_traces: bool = True,
    **kwargs: Never,
) -> DeepAgentDefinition:
    """Define a Managed Deep Agent.

    Accepts the ``create_deep_agent`` surface except platform-owned runtime
    keys, plus selections of managed instructions, MCP servers, sandboxes,
    skills, and the browser when returned from a factory entrypoint.
    Static exported definitions use discovered resources and cannot set those selection fields.
    An omitted selection keeps its discovered default; an empty list selects
    none, and ``sandbox=None`` disables the discovered sandbox (``browser=None``
    the browser). A selected browser needs ``browser.py``, which installs
    Stagehand in the deployment. ``name`` is
    required and becomes the graph id and default deployment name. Factory
    entries must set it with an inline string literal so ``mda build`` can read
    it without running the function.
    The managed runtime owns backend/store/checkpointer/memory and compiles the
    definition into a Deep Agent.

    Parameter types are the LangChain / deepagents types used by
    ``create_deep_agent`` (``BaseTool``, ``SubAgent``, ``AgentMiddleware``,
    ``FilesystemPermission``, …) so authoring stays aligned with the harness.

    A subagent dict with ``"async": True`` runs as a background task in its own
    graph. Unset ``model``, ``tools``, ``permissions``, and ``interrupt_on``
    inherit from this agent; middleware and memory do not. Like a sync subagent,
    it loads only the skill sources its ``skills`` names (``["/skills/"]`` for
    this agent's skills). Its ``update_strategy`` defaults to ``"interrupt"``;
    set ``"enqueue"`` to queue follow-ups without stopping its active run.
    Its ``webhook``, if set, is a URL the Agent Server calls when each of its
    runs finishes. With ``"mode": "fork"`` (experimental) it starts with a copy
    of this agent's conversation, not only the task. Served agents also get a
    background copy of the general-purpose subagent.

    ``metadata`` is MDA-owned authoring config (not a ``create_deep_agent``
    kwarg): the runtime applies it via ``.with_config({"metadata": ...})`` so
    keys such as prompt provenance land on the **root** LangSmith run.
    """
    if not isinstance(name, str) or not name.strip():
        raise TypeError(
            'name is required and must be a non-empty string (e.g. name="research-assistant")'
        )
    if kwargs:
        key = next(iter(kwargs))
        guidance = DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS.get(key)
        if guidance is None:
            raise TypeError(f"define_deep_agent() got an unexpected keyword argument '{key}'")
        raise TypeError(f"Remove `{key}` from define_deep_agent(...). {guidance}")
    # Keep a focused migration error for the retired flag.
    if disable_memory is not None:
        raise TypeError(
            "disable_memory is no longer supported in define_deep_agent(...). "
            "Durable memory is opt-in and declared in memory.py: export `memory` "
            "with `define_memory(agent=MemoryLayer())` to enable it, or leave the file "
            "out to keep no memory (the default). Remove `disable_memory` here."
        )
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    _validate_async_subagents(subagents or ())

    config: dict[str, object] = {
        "name": name,
        **{
            key: value
            for key, value in (
                ("model", model),
                ("tools", tools),
                ("subagents", subagents),
                ("permissions", permissions),
                ("interrupt_on", interrupt_on),
                ("response_format", response_format),
                ("context_schema", context_schema),
                ("cache", cache),
            )
            if value is not None
        },
    }
    if middleware:
        config["middleware"] = middleware
    if debug:
        config["debug"] = debug
    if metadata is not None:
        config["metadata"] = dict(metadata)
    if not replicate_traces:
        config["replicate_traces"] = False

    config.update(copy_managed_selections(instructions, mcp, sandbox, skills, memory, browser))

    return DeepAgentDefinition(config)


_ASYNC_SUBAGENT_UNSUPPORTED_KEYS = ("runnable", "graph_id", "url", "headers")


def _validate_async_subagents(subagents: Sequence[DeepAgentSubAgent]) -> None:
    for spec in subagents:
        if not isinstance(spec, Mapping) or "async" not in spec:
            continue
        if not isinstance(spec["async"], bool):
            raise TypeError('subagent "async" must be True or False')
        if not spec["async"]:
            continue
        if not isinstance(spec.get("description"), str):
            raise TypeError('subagent "async": True requires a `description`')
        skills = spec.get("skills")
        if skills is not None and not _is_path_list(skills):
            raise TypeError('subagent "skills" must be a list of skill source paths')
        _validate_async_run_options(spec)
        unsupported = [key for key in _ASYNC_SUBAGENT_UNSUPPORTED_KEYS if key in spec]
        if unsupported:
            msg = (
                f'subagent "async": True cannot set `{unsupported[0]}`: MDA builds and '
                "launches its graph itself"
            )
            raise TypeError(msg)


def _validate_async_run_options(spec: Mapping[str, object]) -> None:
    """Validate optional fields passed through to async subagent runs."""
    if "update_strategy" in spec and spec["update_strategy"] not in ("interrupt", "enqueue"):
        raise TypeError('subagent "update_strategy" must be "interrupt" or "enqueue"')
    if "webhook" in spec and not isinstance(spec["webhook"], str):
        raise TypeError('subagent "webhook" must be a URL string')
    if "notify_parent" in spec and not isinstance(spec["notify_parent"], bool):
        raise TypeError('subagent "notify_parent" must be True or False')
    if spec.get("mode", "isolated") not in ("isolated", "fork"):
        raise TypeError('subagent "mode" must be "isolated" or "fork"')


def _is_path_list(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str)
        and all(isinstance(path, str) for path in value)
    )


__all__ = [
    "DeepAgentDefinition",
    "GeneratedSkill",
    "McpSelection",
    "SkillSelection",
    "define_deep_agent",
]
