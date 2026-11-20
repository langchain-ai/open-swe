"""Pre-run gate for declarations that need a user-owned connection.

Before the first model turn the gate reads the caller's OAuth grants and
opaque secrets from Agent Auth, and collects any extra pending credentials
(for example LangSmith Managed Tools OAuth). Missing grants raise one
LangGraph interrupt; OAuth slugs include connect URLs, opaque slugs request
password-style input. The tools a grant unlocks are loaded on first use and
cached per caller when the consumer opts in. MCP uses its native listing cache.
Required integrations without a caller end with sign-in guidance.

Consumers that can act as the agent (LangSmith Tool Gateway servers) also
load in runs with no person, as the agent.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

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
from managed_deepagents._agent_auth_http import (
    CredentialStoreUnavailableError,
)
from managed_deepagents._connection_authorization import (
    CredentialAuthorizationDeferredError,
    CredentialAuthorizationRequiredError,
    CredentialAuthorizationRequiredInterrupt,
    PendingCredential,
    collect_pending_user_connection_credentials,
    defer_credential_interrupts,
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
from managed_deepagents._run_input import is_scheduled_execution
from managed_deepagents._run_local import RunLocal
from managed_deepagents._telemetry.otel.context import LogAttributes, record_log
from managed_deepagents._tool_gateway.auth import tool_gateway_context
from managed_deepagents._tool_gateway.connect_first import log_still_not_connected

if TYPE_CHECKING:
    from managed_deepagents.identity import RuntimeIdentity

CREDENTIAL_GATE_MIDDLEWARE_NAME = "mda-credential-gate"

#: How long a caller's loaded tools stay cached.
_TOOL_CACHE_TTL_SECONDS = 5 * 60
#: Resolves a run's credential tools from its runtime.
_LoadTools = Callable[[object], list[BaseTool]]
_CREDENTIAL_UNAVAILABLE = (
    "Credential verification is temporarily unavailable. No tool was executed. Please retry later."
)


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
) -> AgentMiddleware[Any, Any, Any] | None:
    """Install the gate when a declaration names a user-owned connection."""
    if not consumers:
        return None

    return _CredentialGateMiddleware(consumers)


class _CredentialGateMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Reuse loaded tools within one verified run without checkpointing credentials."""

    def __init__(self, consumers: Sequence[CredentialConsumer]) -> None:
        self._consumers = consumers
        self._lock = threading.Lock()
        # Tools loaded for the current run. A cached graph serves concurrent runs
        # with this one instance, so each run keeps its own slot.
        self._runs: RunLocal[list[tuple[str, object, list[BaseTool]] | None]] = RunLocal(
            lambda: [None]
        )

    def _tools(self, runtime: object) -> list[BaseTool]:
        caller = _auth_context(runtime)
        if caller is not None:
            consumers = self._consumers
        elif _agent_owned_schedule(runtime):
            # Only an agent-owned schedule acts as the agent. Channel events, unmapped
            # senders, outside callers and anonymous runs get no Tool Gateway tools.
            consumers = [consumer for consumer in self._consumers if not consumer.requires_identity]
        else:
            consumers = []
        if caller is None and not consumers:
            return []
        contexts = _consumer_contexts(caller, consumers)
        owner: object = caller.context if caller is not None else "agent"
        info = getattr(runtime, "execution_info", None)
        run_id = info.run_id if info is not None else None
        if not run_id:
            return _load_all(consumers, contexts)
        slot = self._runs.get()
        with self._lock:
            loaded = slot[0]
            if loaded is not None and loaded[:2] == (run_id, owner):
                if caller is not None:
                    resolve_user_connection_grants(
                        AgentAuth(caller.context),
                        [
                            credential.slug
                            for credential in _consumer_credentials(self._consumers, caller)
                        ],
                        interrupt=_interrupt,
                    )
                return loaded[2]
            tools = _load_all(consumers, contexts)
            slot[0] = (run_id, owner, tools)
            return tools

    def _prepare(self, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self._runs.clear()
        blocked = _before_agent(self._consumers, runtime, self._tools)
        return dict(blocked) if blocked is not None else None

    @property
    def name(self) -> str:
        return CREDENTIAL_GATE_MIDDLEWARE_NAME

    def before_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        del state
        return self._prepare(runtime)

    async def abefore_agent(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        del state
        return await asyncio.to_thread(self._prepare, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult[Any]:
        tools = _model_tools_for(self._tools, request)
        return handler(_with_extra_tools(request, tools))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        tools = await asyncio.to_thread(_model_tools_for, self._tools, request)
        return await handler(_with_extra_tools(request, tools))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return _wrap_tool_call(self._tools, request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return await _awrap_tool_call(self._tools, request, handler)


hook_config(can_jump_to=["end"])(_CredentialGateMiddleware.before_agent)
hook_config(can_jump_to=["end"])(_CredentialGateMiddleware.abefore_agent)


def _wrap_tool_call(
    load_tools: _LoadTools,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
) -> ToolMessage | Command[Any]:
    try:
        if request.tool is not None:
            return handler(request)
        tools = load_tools(request.runtime)
        return handler(_with_dynamic_tool(request, tools))
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "tool_call")
        return _credential_error_message(request)


async def _awrap_tool_call(
    load_tools: _LoadTools,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    try:
        if request.tool is not None:
            return await handler(request)
        tools = await asyncio.to_thread(load_tools, request.runtime)
        return await handler(_with_dynamic_tool(request, tools))
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "tool_call")
        return _credential_error_message(request)


def _record_credential_unavailable(
    error: CredentialStoreUnavailableError,
    stage: Literal["before_agent", "model_call", "tool_call"],
) -> None:
    attributes: LogAttributes = {"stage": stage, "error.kind": "credential_store_unavailable"}
    if error.status is not None:
        attributes["http.status_code"] = error.status
    record_log("WARN", "credential store unavailable", attributes=attributes)


_TOOL_CREDENTIAL_UNAVAILABLE = (
    "Credential verification is temporarily unavailable. "
    "The tool could not complete. Please retry later."
)


class _CredentialOutageMiddleware(AgentMiddleware[Any, Any, Any]):
    """Turn credential-store outages inside tools into tool errors when no gate is installed."""

    @property
    def name(self) -> str:
        return "CredentialOutageMiddleware"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except CredentialStoreUnavailableError as error:
            _record_credential_unavailable(error, "tool_call")
            return _credential_error_message(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except CredentialStoreUnavailableError as error:
            _record_credential_unavailable(error, "tool_call")
            return _credential_error_message(request)


def create_credential_outage_middleware() -> AgentMiddleware[Any, Any, Any]:
    """Recover from credential-store outages in tools for configs without a gate."""
    return _CredentialOutageMiddleware()


def _credential_error_message(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=_TOOL_CREDENTIAL_UNAVAILABLE,
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


def _before_agent(
    consumers: Sequence[CredentialConsumer], runtime: Runtime[Any], load_tools: _LoadTools
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
            f"resolve_user does not map its {sender} sender to a user of this agent's identity.",
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
        identity = _runtime_identity(runtime)
        user = (identity or {}).get("user") or {}
        if user.get("kind") == "service" or all(
            not consumer.requires_identity for consumer in consumers
        ):
            # Schedules and service callers act for no person: user connections are
            # unavailable, and Tool Gateway servers run as the agent only in an
            # agent-owned schedule.
            if any(consumer.requires_identity for consumer in consumers):
                _warn_no_person()
            return None
        return _sign_in_required(consumers)
    blocked: _BlockedRun | None = None
    try:
        _ensure_grants(consumers, caller)
        load_tools(runtime)
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "before_agent")
        blocked = {
            "messages": [{"role": "assistant", "content": _CREDENTIAL_UNAVAILABLE}],
            "jump_to": "end",
        }
    return blocked


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
    pending = _dedupe_pending_credentials(pending)
    if pending:
        raise_credential_authorization_required(pending, interrupt=_interrupt)


def _dedupe_pending_credentials(
    credentials: Sequence[PendingCredential],
) -> list[PendingCredential]:
    """Every link once.

    Integrations sharing a connection (Gmail and Google Docs on one Google login)
    each keep their own link: each asks only for its own scopes, and the provider
    adds every consent to the connection's grant, so any order connects them all.
    """
    by_link: dict[str, PendingCredential] = {}
    for credential in credentials:
        slug = credential.get("slug")
        if isinstance(slug, str) and slug:
            by_link.setdefault(str(credential.get("auth_id") or slug), credential)
    return list(by_link.values())


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


def _model_tools_for(load_tools: _LoadTools, request: ModelRequest[Any]) -> list[BaseTool]:
    try:
        return load_tools(request.runtime)
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "model_call")
        return []


def _consumer_contexts(
    caller: _Caller | None, consumers: Sequence[CredentialConsumer]
) -> Callable[[CredentialConsumer], AgentAuthContext]:
    """The person for consumers that need one; for Tool Gateway, the person or the agent."""
    gateway = (
        tool_gateway_context(caller.context if caller else None)
        if any(not consumer.requires_identity for consumer in consumers)
        else None
    )

    def context_for(consumer: CredentialConsumer) -> AgentAuthContext:
        if gateway is not None and not consumer.requires_identity:
            return gateway
        if caller is None:
            raise RuntimeError("user connections require a person")
        return caller.context

    return context_for


def _load_all(
    consumers: Sequence[CredentialConsumer],
    contexts: Callable[[CredentialConsumer], AgentAuthContext],
) -> list[BaseTool]:
    if len(consumers) <= 1:
        return [
            tool for consumer in consumers for tool in _cached_tools(consumer, contexts(consumer))
        ]
    resolved = [(consumer, contexts(consumer)) for consumer in consumers]
    with ThreadPoolExecutor(max_workers=len(consumers)) as pool:
        futures: list[Future[list[BaseTool]]] = [
            pool.submit(contextvars.copy_context().run, _deferred_tools, consumer, context)
            for consumer, context in resolved
        ]
    errors = [future.exception() for future in futures]
    pending = [
        credential
        for error in errors
        if isinstance(error, CredentialAuthorizationDeferredError)
        for credential in error.credentials
    ]
    if pending:
        try:
            raise_credential_authorization_required(
                _dedupe_pending_credentials(pending), interrupt=_interrupt
            )
        except CredentialAuthorizationRequiredError:
            # The person answered the card without connecting. Tool Gateway servers
            # run without those connections; a user connection still ends the run.
            if any(
                isinstance(error, CredentialAuthorizationDeferredError)
                and consumer.requires_identity
                for (consumer, _), error in zip(resolved, errors, strict=True)
            ):
                raise
            log_still_not_connected(pending)
    tools: list[BaseTool] = []
    for future, error in zip(futures, errors, strict=True):
        if isinstance(error, CredentialAuthorizationDeferredError):
            tools.extend(cast("list[BaseTool]", error.tools))
        elif error is not None:
            raise error
        else:
            tools.extend(future.result())
    return tools


def _deferred_tools(consumer: CredentialConsumer, context: AgentAuthContext) -> list[BaseTool]:
    defer_credential_interrupts()
    return _cached_tools(consumer, context)


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
    # Acting as the agent (no principal), there are no user grants to read.
    access_tokens = (
        resolve_user_connection_grants(
            AgentAuth(context),
            [credential.slug for credential in consumer.credentials],
            interrupt=_interrupt,
        )
        if context.principal_id
        else {}
    )
    # Managed wrapping preserves each tool's type (``BaseTool`` in, ``BaseTool`` out).
    return [with_managed_runtime(tool) for tool in consumer.load(access_tokens, context)]


def _warn_no_person() -> None:
    warnings.warn(
        "Integrations that need a user connection are unavailable in this run: "
        "it acts for no person (a schedule or a service caller). "
        "LangSmith Tool Gateway servers run as the agent only in agent-owned schedules.",
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


def connecting_caller(runtime: object | None) -> _Caller | None:
    """The caller the pre-run gate would ask; a schedule has no one to answer."""
    caller = _auth_context(runtime)
    if caller is None or is_scheduled_execution(resolve_runtime_configurable(runtime)):
        return None
    return caller


def _interrupt(payload: CredentialAuthorizationRequiredInterrupt) -> None:
    interrupt(payload)


@dataclass(frozen=True)
class _Caller:
    context: AgentAuthContext
    identity: RuntimeIdentity


def _runtime_identity(runtime: object | None) -> RuntimeIdentity | None:
    return build_runtime_identity(resolve_runtime_configurable(runtime))


def _agent_owned_schedule(runtime: object | None) -> bool:
    configurable = resolve_runtime_configurable(runtime)
    identity = build_runtime_identity(configurable)
    return (
        identity is not None
        and identity["user"].get("kind") == "service"
        and is_scheduled_execution(configurable)
    )


def _auth_context(runtime: object | None) -> _Caller | None:
    """The person a run acts for.

    None without a caller, for an agent-owned schedule, for a channel event, for
    a channel sender who cannot be mapped to a user of the agent's identity, or
    for a caller resuming a channel's conversation from outside that channel.
    """
    configurable = resolve_runtime_configurable(runtime)
    identity = build_runtime_identity(configurable)
    if (
        identity is None
        or not identity["user"].get("id")
        or channel_provider(identity)
        or unmapped_channel_provider(identity)
        or outside_caller_on_channel_thread(identity)
    ):
        return None
    if _agent_owned_schedule(runtime):
        # An agent-owned schedule acts for no person.
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
