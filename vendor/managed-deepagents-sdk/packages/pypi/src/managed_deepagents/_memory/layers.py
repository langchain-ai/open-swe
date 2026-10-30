"""Memory layer helpers shared by ``define_memory`` and the runtime.

A layer names *whose* memory is mounted, so it carries the same ``owner`` shape
the credentials API takes: ``{"type": "agent"}`` is the deployment, and
``{"type": "user"}`` is the trusted caller. Resolved memory is always a list of
concrete layers (empty = no memory), agent first. Mounts are always read/write.

``id`` is an assertion, never a selector: the runtime keys a layer on the
authenticated principal and fails the run when a declared ``id`` names someone
else. Mirrors ``resolve_owner_id`` in ``managed_deepagents._credentials`` and
``packages/npm/src/memory/layers.ts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired, TypeAlias, TypedDict, cast

__all__ = [
    "AgentMemoryLayer",
    "MemoryLayer",
    "MemoryLayerType",
    "MemoryLayers",
    "UserMemoryLayer",
    "has_agent_memory_layer",
    "has_user_memory_layer",
    "normalize_memory_layers",
    "user_memory_layer",
]


class AgentMemoryLayer(TypedDict):
    """Deployment-shared memory. Every caller reads and writes the same tree."""

    type: Literal["agent"]
    allow: NotRequired[Callable[[Any], bool | Awaitable[bool]]]


class UserMemoryLayerRequired(TypedDict):
    type: Literal["user"]


class UserMemoryLayer(UserMemoryLayerRequired, total=False):
    """Caller-scoped memory, keyed by the trusted principal.

    ``id`` is optional and is checked, not obeyed: the run fails when it does not
    equal the authenticated principal.
    """

    id: str
    allow: Callable[[Any], bool | Awaitable[bool]]


#: One mounted layer. The union mirrors ``CredentialOwner``.
MemoryLayer: TypeAlias = AgentMemoryLayer | UserMemoryLayer
MemoryLayers: TypeAlias = list[MemoryLayer]
MemoryLayerType: TypeAlias = Literal["agent", "user"]

#: Layer types the runtime can mount, in mount order.
_LAYER_ORDER: tuple[MemoryLayerType, ...] = ("agent", "user")


def has_agent_memory_layer(memory: MemoryLayers) -> bool:
    return any(layer["type"] == "agent" for layer in memory)


def has_user_memory_layer(memory: MemoryLayers) -> bool:
    return any(layer["type"] == "user" for layer in memory)


def user_memory_layer(memory: MemoryLayers) -> UserMemoryLayer | None:
    """The declared user layer, so the runtime can read its asserted ``id``."""
    for layer in memory:
        if layer["type"] == "user":
            return layer
    return None


def normalize_memory_layers(scope: MemoryLayer | Sequence[MemoryLayer] | None) -> MemoryLayers:
    """Normalize authored ``scope`` into a resolved list, agent first.

    Omitted or empty means no durable memory: memory is opt-in, so silence never
    mounts a tree. Every rejection names the object form, because the string
    shorthand this replaced is what a reader of older code will reach for first.
    """
    if scope is None:
        return []
    if isinstance(scope, Mapping):
        return [_normalize_memory_layer(cast("MemoryLayer", scope))]
    if isinstance(scope, (str, bytes)):
        # A bare layer or the retired string shorthand, not a list of layers.
        raise ValueError(
            f"define_memory(...) `scope` must be a list of layers, not {scope!r}. "
            'Write `scope=[{"type": "agent"}]`.'
        )

    resolved: MemoryLayers = []
    for entry in scope:
        layer = _normalize_memory_layer(entry)
        if any(existing["type"] == layer["type"] for existing in resolved):
            raise ValueError(
                f"define_memory(...) `scope` lists the {layer['type']} layer more than once."
            )
        resolved.append(layer)
    return sorted(resolved, key=lambda layer: _LAYER_ORDER.index(layer["type"]))


def _normalize_memory_layer(entry: MemoryLayer) -> MemoryLayer:
    unknown: object = entry
    if not isinstance(unknown, Mapping):
        raise ValueError(
            f"define_memory(...) `scope` entry {unknown!r} is not a layer. Write "
            '`{"type": "agent"}` or `{"type": "user"}`.'
        )
    mapping = cast("Mapping[str, object]", unknown)
    unexpected = sorted(key for key in mapping if key not in {"type", "id", "allow"})
    if unexpected:
        raise ValueError(
            f"define_memory(...) `scope` entry has unknown key(s): {', '.join(unexpected)}. "
            "A layer takes `type` and, for a user layer, `id` and `allow`. "
            "Per-layer mounts, access "
            "modes, and retention are not implemented yet."
        )
    layer_type = mapping.get("type")
    if layer_type == "agent":
        if "id" in mapping:
            raise ValueError(
                "define_memory(...) an agent layer takes no `id`: the deployment owns it. "
                'Write `{"type": "agent"}`.'
            )
        layer: AgentMemoryLayer = {"type": "agent"}
        if "allow" in mapping:
            allow = mapping["allow"]
            if not callable(allow):
                raise ValueError("define_memory(...) an agent layer `allow` must be callable.")
            layer["allow"] = cast("Callable[[Any], bool | Awaitable[bool]]", allow)
        return layer
    if layer_type == "user":
        return _normalize_user_layer(mapping)
    raise ValueError(
        f'define_memory(...) unknown `scope` entry type {layer_type!r}. Use "agent" or "user".'
    )


def _normalize_user_layer(mapping: Mapping[str, object]) -> UserMemoryLayer:
    layer: UserMemoryLayer = {"type": "user"}
    declared = mapping.get("id")
    if declared is not None:
        if not isinstance(declared, str) or not declared.strip():
            raise ValueError("define_memory(...) a user layer `id` must be a non-empty string.")
        layer["id"] = declared
    if "allow" in mapping:
        allow = mapping["allow"]
        if not callable(allow):
            raise ValueError("define_memory(...) a user layer `allow` must be callable.")
        layer["allow"] = cast("Callable[[Any], bool | Awaitable[bool]]", allow)
    return layer
