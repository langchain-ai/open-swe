""":func:`define_memory` — a durable-memory layer selection.

Memory is opt-in. This module provides authoring-time config construction plus
eager validation. The CLI discovers a root ``memory`` export as the fallback; a
dynamic agent may select another definition.

The declaration is configuration, not the memory itself — durable content lives
in managed Context Hub storage, so a deploy never overwrites what earlier runs
learned.

Mirrors ``packages/npm/src/memory/define-memory.ts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar, Generic, Literal, Protocol, TypedDict

from typing_extensions import TypeVar

from managed_deepagents._memory.layers import (
    MemoryLayer as ResolvedMemoryLayer,
)
from managed_deepagents._memory.layers import (
    MemoryLayers,
    normalize_memory_layers,
)
from managed_deepagents.run_context import ManagedRunContext

__all__ = [
    "MemoryConfig",
    "MemoryDefinition",
    "MemoryDefinitionLike",
    "MemoryLayer",
    "define_memory",
]


ContextT = TypeVar("ContextT", default=ManagedRunContext)
AgentContextT = TypeVar("AgentContextT", default=ManagedRunContext)
UserContextT = TypeVar("UserContextT", default=ManagedRunContext)


@dataclass(frozen=True, kw_only=True)
class MemoryLayer(Generic[ContextT]):
    """Enable a memory layer, with an optional per-run context policy.

    Without ``allow``, agent memory is available to all callers and user memory
    is available to trusted people in managed Slack one-to-one DMs.
    Verified Studio users always have user memory access without running its policy.
    """

    allow: Callable[[ContextT], bool | Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        if self.allow is not None and not callable(self.allow):
            raise TypeError("MemoryLayer(...) `allow` must be callable.")


class MemoryConfig(TypedDict):
    """The resolved memory contract the managed runtime reads.

    Versioned because the authoring surface will grow layer types and per-layer
    options: a consumer can branch on ``version`` instead of guessing from shape.
    """

    version: Literal[2]
    #: Mounted layers, agent first (empty = no durable memory).
    scope: MemoryLayers


class MemoryDefinitionLike(Protocol):
    """Minimal surface of :class:`MemoryDefinition` for runtime consumers."""

    @property
    def config(self) -> MemoryConfig: ...


class MemoryDefinition:
    """The pre-runtime spec returned by :func:`define_memory`.

    The CLI can discover this from a root ``memory`` export, or a dynamic agent
    can select it for one run.
    """

    kind: ClassVar[str] = "memory"

    def __init__(self, config: MemoryConfig) -> None:
        self.config = config

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        types = ", ".join(layer["type"] for layer in self.config["scope"]) or "none"
        return f"MemoryDefinition(scope={types})"


#: Reserved keywords from adjacent designs. Naming them keeps a hopeful guess
#: from reading as an unknown typo, and keeps the launch surface one decision wide.
_RESERVED_KEYWORDS: dict[str, str] = {
    "layers": ("Declare independent `agent` and `user` layers."),
    "user_memory": "Use `user=MemoryLayer()` to enable caller-scoped memory.",
    "workspace_memory": "Workspace memory is not designed yet.",
    "organization_memory": "Organization-scoped memory is not supported.",
    "partition": (
        "Claim- and slug-based partitions are not implemented yet; they will be per-layer options."
    ),
    "access": ("Per-layer access modes are not implemented yet; memory mounts are read/write."),
    "mount": "Custom mount paths are not implemented yet.",
    "retention": "Retention policy is not designed yet.",
    "provider": "The durable store is managed; it is not selectable.",
    "sleep_time_compute": "Background memory computation is not designed yet.",
}


def define_memory(
    *,
    agent: MemoryLayer[AgentContextT] | None = None,
    user: MemoryLayer[UserContextT] | None = None,
    scope: ResolvedMemoryLayer | Sequence[ResolvedMemoryLayer] | None = None,
    **kwargs: object,
) -> MemoryDefinition:
    """Declare independent agent and user memory layers.

    Example::

        memory = define_memory(
            agent=MemoryLayer(),
            user=MemoryLayer(allow=allow_user_memory),
        )

    Omitted layers are disabled. Each enabled layer mounts a read/write tree at
    ``/memories/agent/`` or ``/memories/user/``, with ``AGENTS.md`` as hot memory.
    An optional sync or async ``allow(context)`` replaces that layer's default
    policy. The runtime evaluates it once per run, before mounting storage.
    Verified Studio users skip the user memory policy and always have access.
    User memory always requires a trusted person, regardless of its policy.

    ``scope`` accepts the legacy layer declarations. Do not combine it with
    ``agent`` or ``user``.
    """
    _assert_keywords(kwargs)
    if scope is not None and (agent is not None or user is not None):
        raise ValueError("define_memory(...) cannot mix `scope` with `agent` or `user`.")
    layers: MemoryLayers = []
    for name, layer in (("agent", agent), ("user", user)):
        if layer is None:
            continue
        if not isinstance(layer, MemoryLayer):
            raise TypeError(f"define_memory(...) `{name}` must be a MemoryLayer.")
        resolved: ResolvedMemoryLayer = {"type": "agent"} if name == "agent" else {"type": "user"}
        if layer.allow is not None:
            resolved["allow"] = layer.allow
        layers.append(resolved)
    config: MemoryConfig = {
        "version": 2,
        "scope": normalize_memory_layers(scope) if scope is not None else layers,
    }
    return MemoryDefinition(config)


def _assert_keywords(kwargs: dict[str, object]) -> None:
    for key, reason in _RESERVED_KEYWORDS.items():
        if key in kwargs:
            raise ValueError(
                f"define_memory(...) `{key}` is not supported. {reason} "
                "Use `agent=MemoryLayer()`, `user=MemoryLayer()`, or both. "
                "Omit both to keep no memory."
            )
    if kwargs:
        unknown = ", ".join(sorted(kwargs))
        raise ValueError(
            f"define_memory(...) unknown option(s): {unknown}. Valid options: agent, user, scope."
        )
