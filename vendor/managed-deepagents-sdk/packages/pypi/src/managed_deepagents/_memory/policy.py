"""Resolve memory decisions before the graph opens memory storage."""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from managed_deepagents._channels.binding import resolve_channel_info
from managed_deepagents._identity_runtime import (
    build_runtime_identity,
    is_studio_auth_user,
    resolve_runtime_configurable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from managed_deepagents._memory.layers import AgentMemoryLayer, MemoryLayer, UserMemoryLayer
    from managed_deepagents._telemetry.run import UserMemoryDeniedReason


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def is_slack_direct_message(
    context: object, configurable: Mapping[str, object] | None = None
) -> bool:
    if _field(context, "mda_channel") is None:
        return False
    channel = resolve_channel_info(context, configurable)
    return (
        channel is not None
        and channel.provider == "slack"
        and _field(channel.raw_event, "channel_type") == "im"
    )


def _authored_context(context: object, schema: object) -> object:
    """Use the same dict-to-model coercion as native LangGraph context."""
    if (
        isinstance(context, dict)
        and isinstance(schema, type)
        and (issubclass(schema, BaseModel) or is_dataclass(schema))
    ):
        return cast("Callable[..., object]", schema)(**context)
    return context


@dataclass(frozen=True)
class UserMemoryPrincipal:
    id: str
    bypass_policy: bool


def resolve_user_memory_principal(
    configurable: Mapping[str, object] | None,
) -> UserMemoryPrincipal | None:
    identity = build_runtime_identity(configurable)
    if identity is None:
        return None
    if identity["user"]["kind"] == "person":
        return UserMemoryPrincipal(
            identity["user"]["id"],
            is_studio_auth_user((configurable or {}).get("langgraph_auth_user")),
        )
    if os.environ.get("MDA_LOCAL_DEV") == "1" and identity["user"]["id"] == "mda:local-dev":
        principal_id = os.environ.get("MDA_AGENT_AUTH_PRINCIPAL_ID", "").strip()
        if principal_id:
            return UserMemoryPrincipal(principal_id, bypass_policy=True)
    return None


@dataclass(frozen=True)
class UserMemoryAccess:
    """Whether the run may mount user memory.

    A single ``None`` would collapse denials that mean different things, so the
    reason comes back with it.
    """

    user_id: str | None = None
    denied_reason: UserMemoryDeniedReason | None = None


def _denied(reason: UserMemoryDeniedReason) -> UserMemoryAccess:
    return UserMemoryAccess(denied_reason=reason)


async def resolve_user_memory_access(
    layer: UserMemoryLayer | None,
    run_config: Mapping[str, object] | None,
    context: object,
    context_schema: object,
    *,
    selected_by_dynamic_resolver: bool = False,
) -> UserMemoryAccess:
    if layer is None:
        return UserMemoryAccess()
    configurable = resolve_runtime_configurable(run_config)
    principal = resolve_user_memory_principal(configurable)
    if principal is None:
        return _denied("not_a_person")
    user_id = principal.id
    if layer.get("id") is not None and layer["id"] != user_id:
        raise ValueError("user memory layer id must match the authenticated principal")
    if selected_by_dynamic_resolver or principal.bypass_policy:
        return UserMemoryAccess(user_id=user_id)
    if layer.get("allow") is None:
        return (
            UserMemoryAccess(user_id=user_id)
            if is_slack_direct_message(context, configurable)
            else _denied("not_slack_dm")
        )
    if await _evaluate_policy(layer, context, context_schema):
        return UserMemoryAccess(user_id=user_id)
    return _denied("policy_denied")


async def resolve_agent_memory_access(
    layer: AgentMemoryLayer | None,
    context: object,
    context_schema: object,
    *,
    is_run: bool = True,
) -> bool:
    if layer is None:
        return False
    if layer.get("allow") is None:
        return True
    return await _evaluate_policy(layer, context, context_schema) if is_run else False


async def _evaluate_policy(layer: MemoryLayer, context: object, context_schema: object) -> bool:
    allow = layer["allow"]
    name = layer["type"].capitalize()
    try:
        authored = _authored_context(context, context_schema)
    except (NameError, TypeError, ValueError):
        # Invalid policy input cannot grant memory access.
        return False
    try:
        result = allow(authored)
        if inspect.isawaitable(result):
            result = await result
    except Exception:  # noqa: BLE001 - callback failures must not expose context
        raise ValueError(f"{name} memory allow(context) failed") from None
    if not isinstance(result, bool):
        raise ValueError(f"{name} memory allow(context) must return a boolean")
    return result
