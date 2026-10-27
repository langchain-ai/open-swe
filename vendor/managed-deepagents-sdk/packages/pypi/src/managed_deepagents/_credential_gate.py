"""Pre-run gate for declarations that need a user-owned connection.

Before the first model turn the gate reads the caller's OAuth grants and
opaque secrets from Agent Auth, and collects any extra pending credentials
(for example LangSmith Managed Tools OAuth). Missing grants raise one
LangGraph interrupt; OAuth slugs include connect URLs, opaque slugs request
password-style input. The tools a grant unlocks are loaded on first use and
cached per caller when the consumer opts in. MCP uses its native listing cache.
Required integrations without a caller end with sign-in guidance.
"""

from __future__ import annotations

import asyncio
import threading
import time
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    JumpTo,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from managed_deepagents._agent_auth import (
    AgentAuth,
    AgentAuthContext,
)
from managed_deepagents._connection_authorization import (
    CredentialAuthorizationRequiredInterrupt,
    PendingCredential,
    collect_pending_user_connection_credentials,
    raise_credential_authorization_required,
    resolve_user_connection_grants,
)
from managed_deepagents._connections import (
    ConnectionReference,
    channel_provider,
    outside_caller_on_channel_thread,
    resolve_agent_auth_context,
    resolve_owner_id,
    unmapped_channel_provider,
)
from managed_deepagents._identity_runtime import (
    build_runtime_identity,
    resolve_runtime_configurable,
)
from managed_deepagents._managed_tools import with_managed_runtime

if TYPE_CHECKING:
    from managed_deepagents.identity import RuntimeIdentity

CREDENTIAL_GATE_MIDDLEWARE_NAME = "mda-credential-gate"

#: How long a caller's loaded tools stay cached.
_TOOL_CACHE_TTL_SECONDS = 5 * 60


class _BlockedRun(TypedDict):
    # Plain message dicts keep the checkpointed state identical to earlier releases.
    messages: list[dict[str, str]]
    jump_to: JumpTo


@dataclass(frozen=True)
class CredentialConsumer:
    """A declaration whose tools are unlocked by user-owned connections."""

    declaration: object
    credentials: tuple[ConnectionReference, ...]
    load: Callable[[Mapping[str, str], AgentAuthContext], Sequence[BaseTool]]
    #: Cache loaded callables; MCP opts out in favor of native listing caching.
    cache_tools: bool = True
    requires_identity: bool = True
    #: Optional collector for extra pending credentials (no interrupt).
    #: MCP uses this for LangSmith Managed Tools OAuth probes.
    collect_pending_credentials: Callable[[AgentAuthContext], list[PendingCredential]] | None = None


@dataclass
class _CacheEntry:
    tools: list[BaseTool]
    expires_at: float
    declaration: object = field(repr=False)


_tool_cache: dict[tuple[int, str], _CacheEntry] = {}
_tool_cache_lock = threading.Lock()
_tool_load_locks: dict[tuple[int, str], threading.Lock] = {}


def create_credential_gate_middleware(
    consumers: Sequence[CredentialConsumer],
) -> AgentMiddleware | None:
    """Install the gate when a declaration names a user-owned connection."""
    if not consumers:
        return None

    class _CredentialGateMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
        @property
        def name(self) -> str:
            return CREDENTIAL_GATE_MIDDLEWARE_NAME

        def before_agent(
            self, state: AgentState[Any], runtime: Runtime[Any]
        ) -> dict[str, Any] | None:
            del state
            blocked = _before_agent(consumers, runtime)
            return dict(blocked) if blocked is not None else None

        async def abefore_agent(
            self, state: AgentState[Any], runtime: Runtime[Any]
        ) -> dict[str, Any] | None:
            del state
            blocked = await asyncio.to_thread(_before_agent, consumers, runtime)
            return dict(blocked) if blocked is not None else None

        def wrap_model_call(
            self,
            request: ModelRequest[Any],
            handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
        ) -> ModelCallResult[Any]:
            return handler(_with_extra_tools(request, _tools_for(consumers, request)))

        async def awrap_model_call(
            self,
            request: ModelRequest[Any],
            handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
        ) -> ModelCallResult[Any]:
            tools = await asyncio.to_thread(_tools_for, consumers, request)
            return await handler(_with_extra_tools(request, tools))

        def wrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
        ) -> ToolMessage | Command[Any]:
            return handler(_with_dynamic_tool(request, _tools_for(consumers, request)))

        async def awrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
        ) -> ToolMessage | Command[Any]:
            tools = await asyncio.to_thread(_tools_for, consumers, request)
            return await handler(_with_dynamic_tool(request, tools))

    hook_config(can_jump_to=["end"])(_CredentialGateMiddleware.before_agent)
    hook_config(can_jump_to=["end"])(_CredentialGateMiddleware.abefore_agent)
    return _CredentialGateMiddleware()


def _before_agent(
    consumers: Sequence[CredentialConsumer], runtime: Runtime[Any]
) -> _BlockedRun | None:
    channel = channel_provider(_runtime_identity(runtime))
    if channel is not None:
        warnings.warn(
            "Integrations that need a user connection are unavailable in this run: "
            f"a {channel} event runs as the channel, not as a person.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    sender = unmapped_channel_provider(_runtime_identity(runtime))
    if sender is not None:
        warnings.warn(
            "Integrations that need a user connection are unavailable in this run: "
            f"its {sender} sender cannot be mapped to a user of this agent's identity.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    if outside_caller_on_channel_thread(_runtime_identity(runtime)):
        warnings.warn(
            "Integrations that need a user connection are unavailable in this run: "
            "it continues a channel's conversation, which holds content from outside "
            "the workspace.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    caller = _auth_context(runtime)
    if caller is None:
        if all(not consumer.requires_identity for consumer in consumers):
            _warn_missing_optional_identity()
            return None
        return _sign_in_required(consumers)
    _ensure_grants(consumers, caller)
    _load_all(consumers, caller.context)
    return None


def _ensure_grants(
    consumers: Sequence[CredentialConsumer],
    caller: _Caller,
) -> None:
    pending: list[PendingCredential] = []
    credentials = _consumer_credentials(consumers, caller)
    if credentials:
        pending.extend(
            collect_pending_user_connection_credentials(
                AgentAuth(caller.context),
                [credential.slug for credential in credentials],
            )
        )
    for consumer in consumers:
        if consumer.collect_pending_credentials is not None:
            pending.extend(consumer.collect_pending_credentials(caller.context))
    pending = _dedupe_pending_credentials(pending)
    if pending:
        raise_credential_authorization_required(pending, interrupt=_interrupt)


def _dedupe_pending_credentials(
    credentials: Sequence[PendingCredential],
) -> list[PendingCredential]:
    by_slug: dict[str, PendingCredential] = {}
    for credential in credentials:
        slug = credential.get("slug")
        if isinstance(slug, str) and slug:
            by_slug.setdefault(slug, credential)
    return list(by_slug.values())


def _consumer_credentials(
    consumers: Sequence[CredentialConsumer],
    caller: _Caller,
) -> list[ConnectionReference]:
    by_slug: dict[str, ConnectionReference] = {}
    for consumer in consumers:
        for credential in consumer.credentials:
            resolve_owner_id(credential.owner, caller.identity, caller.context.agent_id)
            by_slug.setdefault(credential.slug, credential)
    return list(by_slug.values())


def _tools_for(
    consumers: Sequence[CredentialConsumer], request: ModelRequest[Any] | ToolCallRequest
) -> list[BaseTool]:
    caller = _auth_context(request.runtime)
    if caller is None:
        return []
    return _load_all(consumers, caller.context)


def _load_all(
    consumers: Sequence[CredentialConsumer],
    context: AgentAuthContext,
) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for consumer in consumers:
        tools.extend(_cached_tools(consumer, context))
    return tools


def _cached_tools(
    consumer: CredentialConsumer,
    context: AgentAuthContext,
) -> list[BaseTool]:
    if not consumer.cache_tools:
        return _load_tools(consumer, context)
    key = (id(consumer.declaration), context.principal_id)
    tools = _fresh_tools(key)
    if tools is not None:
        return tools

    with _load_lock(key):
        tools = _fresh_tools(key)
        if tools is not None:
            return tools
        tools = _load_tools(consumer, context)
        with _tool_cache_lock:
            _tool_cache[key] = _CacheEntry(
                tools=tools,
                expires_at=time.monotonic() + _TOOL_CACHE_TTL_SECONDS,
                declaration=consumer.declaration,
            )
        return tools


def _fresh_tools(key: tuple[int, str]) -> list[BaseTool] | None:
    with _tool_cache_lock:
        entry = _tool_cache.get(key)
        if entry is not None and entry.expires_at > time.monotonic():
            return entry.tools
        _tool_cache.pop(key, None)
        return None


def _load_lock(key: tuple[int, str]) -> threading.Lock:
    with _tool_cache_lock:
        return _tool_load_locks.setdefault(key, threading.Lock())


def _load_tools(
    consumer: CredentialConsumer,
    context: AgentAuthContext,
) -> list[BaseTool]:
    agent_auth = AgentAuth(context)
    access_tokens = resolve_user_connection_grants(
        agent_auth,
        [credential.slug for credential in consumer.credentials],
        interrupt=_interrupt,
    )
    # Managed wrapping preserves each tool's type (``BaseTool`` in, ``BaseTool`` out).
    return [with_managed_runtime(tool) for tool in consumer.load(access_tokens, context)]


def _warn_missing_optional_identity() -> None:
    warnings.warn(
        "LangSmith MCP servers skipped: no authenticated caller. Add identity.py to enable them.",
        RuntimeWarning,
        stacklevel=3,
    )


def _with_extra_tools(request: ModelRequest[Any], extra: Sequence[BaseTool]) -> ModelRequest[Any]:
    if not extra:
        return request
    return request.override(tools=[*request.tools, *extra])


def _with_dynamic_tool(request: ToolCallRequest, tools: Sequence[BaseTool]) -> ToolCallRequest:
    if request.tool is not None:
        return request
    name = request.tool_call["name"]
    match = next((tool for tool in tools if tool.name == name), None)
    if match is None:
        return request
    return request.override(tool=match)


def _interrupt(payload: CredentialAuthorizationRequiredInterrupt) -> None:
    interrupt(payload)


@dataclass(frozen=True)
class _Caller:
    context: AgentAuthContext
    identity: RuntimeIdentity


def _runtime_identity(runtime: object | None) -> RuntimeIdentity | None:
    return build_runtime_identity(resolve_runtime_configurable(runtime))


def _auth_context(runtime: object | None) -> _Caller | None:
    """The person a run acts for.

    None without a caller, for a channel event, for a channel sender who cannot
    be mapped to a user of the agent's identity, or for a caller resuming a
    channel's conversation from outside that channel.
    """
    identity = _runtime_identity(runtime)
    if (
        identity is None
        or not identity["user"].get("id")
        or channel_provider(identity)
        or unmapped_channel_provider(identity)
        or outside_caller_on_channel_thread(identity)
    ):
        return None
    return _Caller(context=resolve_agent_auth_context(identity), identity=identity)


def _sign_in_required(consumers: Sequence[CredentialConsumer]) -> _BlockedRun:
    slugs = dict.fromkeys(
        credential.slug for consumer in consumers for credential in consumer.credentials
    )
    return {
        "messages": [
            {
                "role": "assistant",
                "content": (
                    "I cannot continue because these integrations require an authenticated "
                    f"user: {', '.join(slugs)}. "
                    "Retry as an authenticated user and connect these integrations when prompted."
                ),
            }
        ],
        "jump_to": "end",
    }


def clear_credential_tool_cache_for_tests() -> None:
    """Drop every cached caller entry. Test-only."""
    with _tool_cache_lock:
        _tool_cache.clear()
        _tool_load_locks.clear()


__all__ = [
    "CREDENTIAL_GATE_MIDDLEWARE_NAME",
    "CredentialAuthorizationRequiredInterrupt",
    "CredentialConsumer",
    "PendingCredential",
    "clear_credential_tool_cache_for_tests",
    "create_credential_gate_middleware",
]
