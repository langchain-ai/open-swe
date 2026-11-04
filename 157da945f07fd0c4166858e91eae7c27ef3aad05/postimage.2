"""Pre-run gate for declarations that need a user-owned connection.

Before the first model turn the gate reads the caller's OAuth grants and
opaque secrets from Agent Auth, and collects any extra pending credentials
(for example LangSmith Managed Tools OAuth). Missing grants raise one
LangGraph interrupt; OAuth slugs include connect URLs, opaque slugs request
password-style input. The tools a grant unlocks are loaded on first use and
cached per caller when the consumer opts in. MCP uses its native listing cache.
Required integrations without a caller end with sign-in guidance.

A tool call refused until its credentials are connected (LangSmith tool kits
with ``jit_auth``) interrupts the caller and retries that call once on resume.
Without a caller to ask (no caller, or a scheduled run), or still unconnected
after the resume, the call returns a tool error and the run continues.
Calls to tools that declare the credential they need ask once per model
step, before any of them runs.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
import warnings
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    JumpTo,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from managed_deepagents._agent_auth import (
    AgentAuth,
    AgentAuthContext,
)
from managed_deepagents._agent_auth_http import CredentialStoreUnavailableError
from managed_deepagents._connection_authorization import (
    CONNECT_ON_USE_METADATA,
    ConnectOnUse,
    CredentialAuthorizationDeferredError,
    CredentialAuthorizationRequiredInterrupt,
    PendingCredential,
    ToolCredentialRequiredError,
    collect_pending_user_connection_credentials,
    credential_authorization_interrupt,
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
from managed_deepagents._log import logger
from managed_deepagents._managed_tools import with_managed_runtime
from managed_deepagents._run_input import is_scheduled_execution
from managed_deepagents._telemetry.otel.context import LogAttributes, record_log

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
    #: Optional collector for extra pending credentials (no interrupt).
    #: MCP uses this for LangSmith Managed Tools OAuth probes.
    collect_pending_credentials: Callable[[AgentAuthContext], list[PendingCredential]] | None = None
    #: Lists connect links for the slugs of each server whose tools ask on use.
    connect_links: (
        Callable[[AgentAuthContext, Mapping[str, Collection[str]]], list[PendingCredential]] | None
    ) = None


class _ConnectState(AgentState[Any]):
    """The connect prompt one model step's calls need, asked once after the step."""

    #: Links for the credentials the last model step's calls need, asked once after it.
    mda_connect_request: NotRequired[Annotated[list[PendingCredential] | None, PrivateStateAttr]]
    #: Slugs asked about this step; their refused calls do not ask again.
    mda_connect_asked: NotRequired[Annotated[list[str], PrivateStateAttr]]


class _ConnectUpdate(TypedDict, total=False):
    """A state update to ``_ConnectState``'s keys."""

    mda_connect_request: list[PendingCredential] | None
    mda_connect_asked: list[str]


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


class _CredentialGateMiddleware(AgentMiddleware[_ConnectState, Any, Any]):
    """Reuse loaded tools within one verified run without checkpointing credentials."""

    state_schema = _ConnectState

    def __init__(self, consumers: Sequence[CredentialConsumer]) -> None:
        self._consumers = consumers
        self._lock = threading.Lock()
        self._loaded: tuple[str, AgentAuthContext, list[BaseTool]] | None = None

    def _tools(self, runtime: object) -> list[BaseTool]:
        caller = _auth_context(runtime)
        if caller is None:
            return []
        info = getattr(runtime, "execution_info", None)
        run_id = info.run_id if info is not None else None
        if not run_id:
            return _load_all(self._consumers, caller.context)
        with self._lock:
            if self._loaded is not None and self._loaded[:2] == (run_id, caller.context):
                resolve_user_connection_grants(
                    AgentAuth(caller.context),
                    [
                        credential.slug
                        for credential in _consumer_credentials(self._consumers, caller)
                    ],
                    interrupt=_interrupt,
                )
                return self._loaded[2]
            tools = _load_all(self._consumers, caller.context)
            self._loaded = (run_id, caller.context, tools)
            return tools

    def _prepare(self, runtime: Runtime[Any]) -> dict[str, Any] | None:
        with self._lock:
            self._loaded = None
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
        response = handler(_with_extra_tools(request, tools))
        return _with_connect_request(self._consumers, request, tools, response)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        tools = await asyncio.to_thread(_model_tools_for, self._tools, request)
        response = await handler(_with_extra_tools(request, tools))
        return await asyncio.to_thread(
            _with_connect_request, self._consumers, request, tools, response
        )

    def after_model(self, state: _ConnectState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        del runtime
        update = _ask_once(state)
        return dict(update) if update is not None else None

    async def aafter_model(
        self, state: _ConnectState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

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
    if request.tool is not None:
        return handler(request)
    try:
        tools = load_tools(request.runtime)
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "tool_call")
        return _credential_error_message(request)
    return _call_connecting(_with_dynamic_tool(request, tools), handler)


async def _awrap_tool_call(
    load_tools: _LoadTools,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    if request.tool is not None:
        return await handler(request)
    try:
        tools = await asyncio.to_thread(load_tools, request.runtime)
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "tool_call")
        return _credential_error_message(request)
    return await _acall_connecting(_with_dynamic_tool(request, tools), handler)


def _record_credential_unavailable(
    error: CredentialStoreUnavailableError,
    stage: Literal["before_agent", "model_call", "tool_call"],
) -> None:
    attributes: LogAttributes = {"stage": stage, "error.kind": "credential_store_unavailable"}
    if error.status is not None:
        attributes["http.status_code"] = error.status
    record_log("WARN", "credential store unavailable", attributes=attributes)


def _credential_error_message(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=_CREDENTIAL_UNAVAILABLE,
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
        if all(not consumer.requires_identity for consumer in consumers):
            _warn_missing_optional_identity()
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


def _model_tools_for(load_tools: _LoadTools, request: ModelRequest[Any]) -> list[BaseTool]:
    try:
        return load_tools(request.runtime)
    except CredentialStoreUnavailableError as error:
        _record_credential_unavailable(error, "model_call")
        return []


def _load_all(
    consumers: Sequence[CredentialConsumer],
    context: AgentAuthContext,
) -> list[BaseTool]:
    if len(consumers) <= 1:
        return [tool for consumer in consumers for tool in _cached_tools(consumer, context)]
    with ThreadPoolExecutor(max_workers=len(consumers)) as pool:
        futures: list[Future[list[BaseTool]]] = [
            pool.submit(contextvars.copy_context().run, _deferred_tools, consumer, context)
            for consumer in consumers
        ]
    errors = [future.exception() for future in futures]
    pending = [
        credential
        for error in errors
        if isinstance(error, CredentialAuthorizationDeferredError)
        for credential in error.credentials
    ]
    if pending:
        raise_credential_authorization_required(
            _dedupe_pending_credentials(pending), interrupt=_interrupt
        )
    for error in errors:
        if error is not None:
            raise error
    return [tool for future in futures for tool in future.result()]


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


def _call_connecting(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
) -> ToolMessage | Command[Any]:
    try:
        return handler(request)
    except Exception as error:
        missing = _credential_refusal(error)
        if missing is None:
            raise
    if not _ask_to_connect(request, missing):
        return _not_connected(request, missing)
    # A resume value is never evidence of a connection; only the retry decides.
    try:
        return handler(request)
    except Exception as error:
        refusal = _credential_refusal(error)
        if refusal is None:
            raise
        return _not_connected(request, refusal)


async def _acall_connecting(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    try:
        return await handler(request)
    except Exception as error:
        missing = _credential_refusal(error)
        if missing is None:
            raise
    if not _ask_to_connect(request, missing):
        return _not_connected(request, missing)
    try:
        return await handler(request)
    except Exception as error:
        refusal = _credential_refusal(error)
        if refusal is None:
            raise
        return _not_connected(request, refusal)


def _credential_refusal(error: BaseException) -> ToolCredentialRequiredError | None:
    """The refusal behind ``error``, through any middleware that wrapped it."""
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ToolCredentialRequiredError):
            return cause
        cause = cause.__cause__
    return None


def _with_connect_request(
    consumers: Sequence[CredentialConsumer],
    request: ModelRequest[Any],
    tools: Sequence[BaseTool],
    response: ModelResponse[Any],
) -> ModelCallResult[Any]:
    """List fresh links for the credentials this step's calls need, for ``after_model``.

    Tools load once per run, so links are listed here rather than read from the
    tools. Listing in the model step keeps a resumed run, which replays
    ``after_model`` but not the model call, from starting new connect sessions.
    """
    update: _ConnectUpdate = {}
    needed = _connect_on_use(tools, response)
    caller = _connecting_caller(request.runtime) if needed else None
    links = _connect_links(consumers, caller.context, needed) if caller is not None else []
    if links:
        update["mda_connect_request"] = links
    elif request.state.get("mda_connect_request"):
        # An unanswered prompt from an earlier turn must not be asked again.
        update["mda_connect_request"] = None
    if request.state.get("mda_connect_asked"):
        update["mda_connect_asked"] = []
    if not update:
        return response
    return ExtendedModelResponse(model_response=response, command=Command(update=update))


def _connect_on_use(
    tools: Sequence[BaseTool], response: ModelResponse[Any]
) -> dict[str, ConnectOnUse]:
    """The credentials stored tools need for the calls in ``response``, by slug."""
    connectable: dict[str, ConnectOnUse] = {
        tool.name: tool.metadata[CONNECT_ON_USE_METADATA]
        for tool in tools
        if tool.metadata and CONNECT_ON_USE_METADATA in tool.metadata
    }
    needed: dict[str, ConnectOnUse] = {}
    if not connectable:
        return needed
    for message in response.result:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            connect = connectable.get(call["name"])
            if connect is not None:
                needed.setdefault(connect["credential"]["slug"], connect)
    return needed


def _connect_links(
    consumers: Sequence[CredentialConsumer],
    context: AgentAuthContext,
    needed: Mapping[str, ConnectOnUse],
) -> list[PendingCredential]:
    """Fresh links for the needed slugs; a provider connected since load is not listed."""
    wanted: dict[str, set[str]] = {}
    for slug, connect in needed.items():
        wanted.setdefault(connect["server"], set()).add(slug)
    linked: list[PendingCredential] = []
    for consumer in consumers:
        if consumer.connect_links is None:
            continue
        try:
            linked.extend(consumer.connect_links(context, wanted))
        except Exception as error:  # noqa: BLE001 - each refused call still asks with its 428's link
            logger.warning("could not list connect links: %s", logger.safe_error_label(error))
    by_slug = {credential["slug"]: credential for credential in reversed(linked)}
    return [by_slug[slug] for slug in needed if slug in by_slug]


def _ask_once(state: _ConnectState) -> _ConnectUpdate | None:
    credentials = state.get("mda_connect_request")
    if not credentials:
        return None
    _interrupt(credential_authorization_interrupt(credentials))
    return {
        "mda_connect_request": None,
        "mda_connect_asked": [credential["slug"] for credential in credentials],
    }


def _ask_to_connect(request: ToolCallRequest, missing: ToolCredentialRequiredError) -> bool:
    """Interrupt with the connect links when this run's caller can follow them.

    A credential already asked about this step returns its tool error instead.
    """
    asked = request.state.get("mda_connect_asked") or ()
    if (
        not missing.credentials
        or all(credential["slug"] in asked for credential in missing.credentials)
        or _connecting_caller(request.runtime) is None
    ):
        return False
    _interrupt(credential_authorization_interrupt(missing.credentials))
    return True


def _connecting_caller(runtime: object | None) -> _Caller | None:
    """The caller the pre-run gate would ask; a schedule has no one to answer."""
    caller = _auth_context(runtime)
    if caller is None or is_scheduled_execution(resolve_runtime_configurable(runtime)):
        return None
    return caller


def _not_connected(request: ToolCallRequest, missing: ToolCredentialRequiredError) -> ToolMessage:
    return ToolMessage(
        content=f"{missing.provider} is not connected. Connect it in LangSmith, then try again.",
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


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
