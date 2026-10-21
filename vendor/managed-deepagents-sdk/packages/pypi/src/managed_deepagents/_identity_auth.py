"""Managed custom-auth handler built from a declared identity.

Consumed only by the generated ``_mda_auth.py`` module, which calls
:func:`build_managed_auth` to produce a configured ``langgraph_sdk.Auth``. The
handler resolves the caller identity and scopes threads/store for both
``trusted_backend`` and ``validated_token`` ingress modes.

"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from importlib import import_module
from ssl import SSLCertVerificationError
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import UUID

from langgraph_sdk import Auth
from langgraph_sdk.auth import is_studio_user
from langgraph_sdk.auth.types import (
    AuthContext,
    BaseUser,
    CronsCreate,
    CronsDelete,
    CronsRead,
    CronsSearch,
    CronsUpdate,
    FilterType,
    RunsCreate,
    ThreadsCreate,
    ThreadsDelete,
    ThreadsRead,
    ThreadsSearch,
    ThreadsUpdate,
)

from managed_deepagents._identity_runtime import (
    CHANNEL_THREAD_METADATA,
    _field,
    _runtime_user_id,
    build_runtime_identity,
)
from managed_deepagents._log import logger
from managed_deepagents._schedule_auth import (
    ScheduleHandoffUser,
    authorize_schedule,
    schedule_access,
    schedule_handoff_user,
)

#: Value shapes the ``auth.on.threads`` / ``auth.on.crons`` handlers receive.
ThreadsValue = (
    ThreadsCreate | ThreadsRead | ThreadsUpdate | ThreadsDelete | ThreadsSearch | RunsCreate
)
CronsValue = CronsCreate | CronsRead | CronsUpdate | CronsDelete | CronsSearch

HeaderName = str | bytes
HeaderValue = str | bytes | None
HeadersLike = Mapping[HeaderName, HeaderValue] | Iterable[tuple[HeaderName, HeaderValue]] | None


class HttpRequestLike(Protocol):
    """Minimal request surface used by :func:`resolve_request_identity`."""

    @property
    def headers(self) -> HeadersLike: ...


class ManagedUserRequired(TypedDict):
    identity: str
    permissions: list[str]
    mda_user_id: str


class ManagedUser(ManagedUserRequired, total=False):
    """User object returned by managed auth and stored in ``configurable``."""

    mda_user_email: str
    #: Resolved group memberships; read-only, never an isolation key.
    mda_groups: list[str]
    mda_claims: JsonObject
    mda_source_provider: str
    mda_source_thread_id: str
    mda_user_kind: Literal["user", "service"]
    #: ``langgraph_sdk.auth.types.MinimalUserDict`` display fields.
    is_authenticated: bool
    display_name: str
    #: Validated-token provider id for Agent Auth principal resolve.
    mda_identity_provider: str
    #: Verified IdP issuer (``iss``) for Agent Auth principal resolve.
    mda_identity_issuer: str
    #: Verified IdP subject (``sub``) for Agent Auth principal resolve.
    mda_identity_subject: str
    #: Agent Auth principal UUID when Trigger ``identity_context`` already resolved one.
    mda_agent_auth_principal_id: str


if TYPE_CHECKING:
    from managed_deepagents.identity import (
        HttpIngress,
        IdentityConfig,
        IdentityDefinitionLike,
        JsonObject,
        RuntimeIdentity,
        ValidatedTokenIngress,
    )

__all__ = [
    "ManagedAuthError",
    "ManagedUser",
    "build_managed_auth",
    "channel_events_bypass_user",
    "clear_langsmith_verification_cache_for_tests",
    "is_channel_events_request",
    "resolve_request_identity",
]

#: Reserved transport for the trusted-backend ingress mode. The caller id
#: travels as a reserved header (allowlisted into ``config.configurable`` by the
#: generated ``langgraph.json``); the ingress secret is validated here and is
#: deliberately NOT allowlisted, so it never reaches ``config.configurable``.
USER_HEADER = "x-mda-user-id"
INGRESS_SECRET_HEADER = "x-mda-ingress-secret"  # noqa: S105 - header name, not a secret
#: Internal handoff headers — accepted after a server API key or legacy ingress secret validates.
SOURCE_PROVIDER_HEADER = "x-mda-source-provider"
SOURCE_THREAD_HEADER = "x-mda-source-thread-id"
#: Trigger loopback stamp for a verified identity_context principal UUID.
AGENT_AUTH_PRINCIPAL_HEADER = "x-mda-agent-auth-principal-id"

#: Deployment env var carrying the shared secret the trusted backend presents.
INGRESS_SECRET_ENV = "MDA_INGRESS_SECRET"  # noqa: S105 - env var name, not a secret
HANDOFF_SECRET_ENV = "MDA_HANDOFF_SECRET"  # noqa: S105 - env var name, not a secret
HANDOFF_SECRET_HEADER = "x-mda-handoff-secret"  # noqa: S105 - header name, not a secret

#: Platform-owned LangSmith auth configuration.
LANGSMITH_AUTH_ENDPOINT_ENV = "LANGSMITH_AUTH_ENDPOINT"
LANGSMITH_TENANT_ID_ENV = "LANGSMITH_TENANT_ID"
LANGSMITH_API_KEY_HEADER = "x-api-key"
LANGSMITH_API_KEY_ENV = "LANGSMITH_API_KEY"
LANGCHAIN_API_KEY_ENV = "LANGCHAIN_API_KEY"

#: Set to ``"1"`` by ``mda dev`` so local Studio / API clients can exercise
#: ``trusted_backend`` identity without injecting ingress headers. Never set in
#: deploy.
LOCAL_DEV_ENV = "MDA_LOCAL_DEV"

#: Synthetic actor for local ``mda dev`` when ingress headers are absent.
LOCAL_DEV_USER = "mda:local-dev"

#: Channel Events routes authenticate via provider signatures (Slack HMAC),
#: not MDA ingress. Path shape: ``/channels/{name}/events``.
CHANNEL_EVENTS_PATH_PREFIX = "/channels/"
CHANNEL_EVENTS_PATH_SUFFIX = "/events"
TRIGGER_CHANNEL_EVENTS_PATH_PREFIX = "/v1/channels/"

#: Connector Events routes authenticate via provider signatures (GitHub HMAC),
#: not MDA ingress. Path shape: ``/connectors/{name}/events``.
CONNECTOR_EVENTS_PATH_PREFIX = "/connectors/"
CONNECTOR_EVENTS_PATH_SUFFIX = "/events"

#: Synthetic actor so middleware_first allows unsigned Slack Events ingress.
CHANNEL_EVENTS_USER = "mda:channel-events"
CHANNEL_SERVICE_USER = "mda:channel"

#: Owner-key namespace for threads owned by a channel source conversation.
CHANNEL_THREAD_OWNER_PREFIX = "mda:source-thread:"


def _is_trusted_source_provider(provider: str) -> bool:
    # Lazy: avoid import cycles at auth module load.
    from managed_deepagents._channels.source_providers import (  # noqa: PLC0415
        is_trusted_source_provider,
    )

    return is_trusted_source_provider(provider)


class ManagedAuthError(Exception):
    """A fail-closed auth rejection, translated to an HTTP error by the handler."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _is_resume_only(value: object) -> bool:
    """Whether a run only answers the interrupts of an existing thread.

    A ``command`` with nothing but a ``resume`` value, no new input, and no
    webhook to report the thread elsewhere. Encrypted or otherwise opaque run
    payloads are not.

    Studio resumes with ``multitask_strategy: "rollback"``, the thread's
    checkpoint and the user's breakpoints, so none of those can be refused.
    """
    if not isinstance(value, Mapping):
        return False
    thread_id = value.get("thread_id")
    kwargs = value.get("kwargs")
    # The Agent Server passes a ``UUID``; a JSON round trip, a string.
    if not isinstance(thread_id, UUID | str) or not str(thread_id).strip():
        return False
    if not isinstance(kwargs, Mapping) or kwargs.get("input") is not None:
        return False
    if _changes_more_than_the_resume(value, kwargs):
        return False
    command = kwargs.get("command")
    return (
        isinstance(command, Mapping)
        and set(command) == {"resume"}
        and command.get("resume") is not None
    )


def _changes_more_than_the_resume(
    value: Mapping[str, object], kwargs: Mapping[str, object]
) -> bool:
    """Whether a run would report the thread elsewhere or unmark it.

    The run's metadata merges over the thread's, so it may not name the
    channel-thread marker.
    """
    if kwargs.get("webhook") is not None or _claims_channel_thread(value.get("metadata")):
        return True
    config = kwargs.get("config")
    if config is None:
        return False
    return not isinstance(config, Mapping) or _claims_channel_thread(config.get("metadata"))


def _claims_channel_thread(metadata: object) -> bool:
    """Whether run metadata names the channel-thread marker at all."""
    if metadata is None:
        return False
    return not isinstance(metadata, Mapping) or CHANNEL_THREAD_METADATA in metadata


def _studio_may_run(value: object) -> bool:
    """Whether a Studio run only resumes interrupts, so it may run on channel threads."""
    if _is_resume_only(value):
        return True
    # Owner-scoped instead, so Studio sees "not found"; say why, by shape only.
    logger.info(
        "studio run is not a resume, so it is owner-scoped %s", json.dumps(_run_shape(value))
    )
    return False


def _run_shape(value: object) -> dict[str, object]:
    """What a run payload holds, without its values, to explain a refusal."""
    if not isinstance(value, Mapping):
        return {"value": type(value).__name__}
    kwargs = value.get("kwargs")
    command = kwargs.get("command") if isinstance(kwargs, Mapping) else None
    return {
        "threadId": type(value.get("thread_id")).__name__,
        "kwargs": type(kwargs).__name__,
        "input": type(kwargs.get("input")).__name__ if isinstance(kwargs, Mapping) else "missing",
        "webhook": isinstance(kwargs, Mapping) and kwargs.get("webhook") is not None,
        "command": (
            [logger.log_identifier(str(key)) for key in list(command)[:8]]
            if isinstance(command, Mapping)
            else type(command).__name__
        ),
    }


def _stamp_thread_metadata(payload: JsonObject, user: object, key: str, action: str) -> None:
    """Stamp the caller's owner key on a thread payload's metadata.

    Only managed ingress marks a thread as a channel's; no client can. Searches
    keep finding threads made before the marker existed.
    """
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    stamped = cast("JsonObject", metadata)
    stamped["owner"] = key
    stamped.pop(CHANNEL_THREAD_METADATA, None)
    if action != "search" and _channel_conversation_owner_key(user) is not None:
        stamped[CHANNEL_THREAD_METADATA] = "true"
    # A run's config metadata merges into what the run sees as well, so a run
    # can't claim the marker there either.
    _drop_channel_thread_marker(payload.get("config"))


def _drop_channel_thread_marker(holder: object) -> None:
    """Remove the channel-thread marker from a payload's ``metadata``.

    A run's metadata is its assistant's, thread's, config's and own metadata
    merged, and only managed ingress may mark a channel's thread.
    """
    if isinstance(holder, dict):
        metadata = holder.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop(CHANNEL_THREAD_METADATA, None)


def build_managed_auth(identity: IdentityDefinitionLike) -> Auth:
    """Build the managed ``langgraph_sdk.Auth`` handler from a declared identity."""
    cfg: IdentityConfig = identity.config
    http: HttpIngress = cfg["ingress"]["http"]
    auth = Auth()

    async def authenticate(
        headers: HeadersLike = None,
        authorization: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> ManagedUser | ScheduleHandoffUser:
        return await _authenticate(cfg, http, headers, authorization, path, method)

    # ``auth.authenticate`` / ``auth.on`` / ``auth.on.threads`` /
    # ``auth.on.store`` are decorators; call them directly to register each
    # handler. Using the call form (rather than ``@auth.authenticate``) keeps
    # every local binding referenced, so unused-code linters can flag genuinely
    # dead handlers.
    auth.authenticate(authenticate)
    auth.on(_scope_any)
    auth.on.threads(_scope_threads)
    auth.on.crons(_scope_crons)
    auth.on.store(_scope_store)

    return auth


async def _scope_threads(ctx: AuthContext, value: ThreadsValue) -> FilterType | None:
    # Let Studio inspect every deployment thread so channel-triggered runs
    # and their interrupts are visible there, and answer those interrupts on
    # channel threads, which channels link to. Keep other mutations
    # owner-scoped: Studio identity is reachable by any holder of a workspace
    # LangSmith API key, so exempting all actions would let that key alter
    # caller threads or speak in a channel's conversation.
    user = ctx.user
    if ctx.action == "search" and schedule_access(user) is not None:
        return None
    if is_local_dev_unscoped_user(user):
        return None
    studio = is_studio_user(user)
    if studio and ctx.action in {"read", "search"}:
        return None
    if studio and ctx.action == "create_run" and _studio_may_run(value):
        return {CHANNEL_THREAD_METADATA: "true"}
    key = _thread_owner_key(user)
    if isinstance(value, dict):
        # Stamp ownership on every threads payload, including the actions
        # whose upstream TypedDict does not declare ``metadata``.
        _stamp_thread_metadata(cast("JsonObject", value), user, key, ctx.action)
    return {"owner": key}


async def _scope_store(ctx: AuthContext, value: dict[str, Any]) -> None:
    """Store handler; ``auth.on.store`` hands every action value as ``dict[str, Any]``.

    Each value (``StoreGet`` … ``StoreListNamespaces``) carries ``namespace``.
    The reserved-table check runs before any Studio / local-dev exemption on
    purpose. A caller holding a workspace LangSmith key can present
    ``x-auth-scheme: langsmith`` and be authenticated as a Studio user by the
    Agent Server, so exempting Studio first would hand out the deployment-wide
    account-link and legacy-credential tables to anyone with that key.
    """
    try:
        _assert_store_access(ctx.user, value)
    except ManagedAuthError as err:
        raise _http_exception(err) from err


async def _scope_any(ctx: AuthContext, value: dict[str, Any]) -> None:
    """Global (catch-all) authorization handler; the SDK requires ``value``.

    LangGraph authorization is fail-open: an unhandled resource is permitted,
    so the resources MDA does not scope (``assistants``) would otherwise be
    reachable unfiltered by any authenticated caller. The specific handlers
    registered alongside this one take precedence over it, so threads and
    store keep their own scoping.
    """
    # An assistant's metadata merges into its runs' too, so no caller, Studio
    # included, may mark an assistant as a channel's.
    if ctx.resource == "assistants" and isinstance(value, dict):
        _drop_channel_thread_marker(value)
        _drop_channel_thread_marker(value.get("config"))
    if _is_unscoped_principal(ctx.user):
        return
    try:
        _deny_unless_assistant_read(ctx.resource, ctx.action)
    except ManagedAuthError as err:
        raise _http_exception(err) from err


async def _scope_crons(ctx: AuthContext, value: CronsValue) -> FilterType | None:
    try:
        return await authorize_schedule(ctx.user, ctx.action, value)
    except ValueError as err:
        raise _http_exception(ManagedAuthError(403, "Schedule access is not permitted")) from err


def _is_unscoped_principal(user: BaseUser) -> bool:
    """True for the principals exempt from managed scoping (Studio, local dev)."""
    return is_studio_user(user) or is_local_dev_unscoped_user(user)


def _deny_unless_assistant_read(resource: str, action: str) -> None:
    """Reject (403) every resource/action pair except reading or searching assistants.

    This covers the resources managed auth does not scope per caller.

    Cron access uses the dedicated schedule handler.

    Assistant reads stay open so clients can enumerate the deployment's agents;
    that exposes ``graph_id`` and assistant config, which is deploy-authored
    rather than caller data.
    """
    if resource == "assistants" and action in {"read", "search"}:
        return
    raise ManagedAuthError(403, f"{resource}:{action} is not permitted for this caller")


async def _authenticate(
    cfg: IdentityConfig,
    http: HttpIngress,
    headers: HeadersLike,
    authorization: str | None,
    path: str | None,
    method: str | None,
) -> ManagedUser | ScheduleHandoffUser:
    """Resolve the caller identity, bypassing auth for public channel Events routes."""
    # Provider-signed channel Events must reach the public HTTP handler before
    # MDA ingress can succeed (Slack cannot present ``x-mda-ingress-secret``).
    if is_channel_events_request(method, path):
        return channel_events_bypass_user()
    normalized = _normalize_headers(headers)
    try:
        try:
            schedule_user = schedule_handoff_user(normalized, path, method)
        except ValueError as err:
            raise ManagedAuthError(403, "Schedule access is not permitted") from err
        if schedule_user is not None:
            return schedule_user
        managed_handoff = _resolve_managed_handoff_user(normalized)
        if managed_handoff is not None:
            return managed_handoff
        if http == "backend" or _has_valid_ingress_secret(normalized):
            return _resolve_trusted_backend_user(cfg, normalized)
        _reject_untrusted_handoff_headers(normalized)
        if _is_langsmith_api_key_auth(http):
            return await _resolve_langsmith_api_key_user(normalized)
        # validated_token verification does blocking JWKS/introspection HTTP;
        # run it off the event loop. Imported lazily to avoid a circular import
        # (``_validated_token`` imports ``ManagedAuthError`` from this module).
        resolve = import_module("managed_deepagents._validated_token").resolve_validated_token_user
        validated_http = cast("ValidatedTokenIngress", http)
        return await asyncio.to_thread(resolve, cfg, validated_http, normalized, authorization)
    except ManagedAuthError as err:
        raise _http_exception(err) from err


async def resolve_request_identity(
    cfg: IdentityConfig, request: HttpRequestLike
) -> RuntimeIdentity:
    """Resolve the caller's frozen identity envelope from a raw HTTP request.

    Uses the same ingress logic (``trusted_backend`` headers / ``validated_token``
    verification) as the managed auth handler, then builds the frozen identity
    envelope. Raises :class:`ManagedAuthError` (401) when the caller is
    unauthenticated or unresolvable.

    This is the resolver that backs default-secure connector HTTP routes.
    """
    headers = _normalize_headers(getattr(request, "headers", None))
    authorization = headers.get("authorization")
    http: HttpIngress = cfg["ingress"]["http"]
    managed_handoff = _resolve_managed_handoff_user(headers)
    trusted_handoff = managed_handoff is not None or _has_valid_ingress_secret(headers)
    if managed_handoff is not None:
        user = managed_handoff
    elif http == "backend" or trusted_handoff:
        user = _resolve_trusted_backend_user(cfg, headers)
    else:
        _reject_untrusted_handoff_headers(headers)
        if _is_langsmith_api_key_auth(http):
            user = await _resolve_langsmith_api_key_user(headers)
        else:
            # validated_token verification does blocking JWKS/introspection HTTP; run
            # it off the event loop. Imported lazily to avoid a circular import.
            resolve = import_module(
                "managed_deepagents._validated_token"
            ).resolve_validated_token_user
            validated_http = cast("ValidatedTokenIngress", http)
            user = await asyncio.to_thread(resolve, cfg, validated_http, headers, authorization)
    stamped = (
        dict(user)
        if isinstance(user, Mapping)
        else {"mda_user_id": getattr(user, "mda_user_id", None)}
    )
    # Preserve trusted channel source stamps; default public HTTP to "http".
    if "mda_source_provider" not in stamped and not trusted_handoff:
        stamped["mda_source_provider"] = "http"
    identity = build_runtime_identity({"langgraph_auth_user": stamped})
    if identity is None:
        raise ManagedAuthError(401, "unauthenticated")
    return identity


def _is_events_path(path_value: str, prefix: str, suffix: str) -> bool:
    if not path_value.startswith(prefix) or not path_value.endswith(suffix):
        return False
    middle = path_value[len(prefix) : -len(suffix)]
    return bool(middle) and "/" not in middle


def is_channel_events_request(method: str | None, path: str | None) -> bool:
    """True when the request targets a public channel/connector Events ingress route."""
    if method is None or path is None:
        return False
    if str(method).upper() != "POST":
        return False
    path_value = str(path).rstrip("/")
    # Host keeps the deployment mount prefix in auth requests. Strip only the
    # server-configured prefix at a complete path-segment boundary.
    mount_prefix = os.environ.get("MOUNT_PREFIX", "").rstrip("/")
    if mount_prefix.startswith("/") and path_value.startswith(f"{mount_prefix}/"):
        path_value = path_value[len(mount_prefix) :]
    return (
        _is_events_path(path_value, CHANNEL_EVENTS_PATH_PREFIX, CHANNEL_EVENTS_PATH_SUFFIX)
        or _is_events_path(
            path_value, TRIGGER_CHANNEL_EVENTS_PATH_PREFIX, CHANNEL_EVENTS_PATH_SUFFIX
        )
        or _is_events_path(path_value, CONNECTOR_EVENTS_PATH_PREFIX, CONNECTOR_EVENTS_PATH_SUFFIX)
    )


def channel_events_bypass_user() -> ManagedUser:
    """Placeholder identity so middleware allows provider-signed channel Events."""
    return {
        "identity": CHANNEL_EVENTS_USER,
        "permissions": [],
        "mda_user_id": CHANNEL_EVENTS_USER,
    }


def _resolve_trusted_backend_user(
    cfg: IdentityConfig, headers: Mapping[str, object]
) -> ManagedUser:
    """Validate the ingress secret, then trust the reserved user identity headers.

    Under ``mda dev`` (``MDA_LOCAL_DEV=1``), requests without a valid ingress
    secret resolve to a synthetic local service principal so LangGraph Studio
    can talk to the agent without custom headers. Deploy never sets that env.

    Optional source-provider / source-thread headers are accepted only after the
    ingress secret validates (managed channel loopback handoff). Organization
    headers are ignored — organization identity is not supported.
    """
    local = try_local_dev_trusted_user(cfg, headers)
    if local is not None:
        return local

    _require_ingress_secret(headers)

    return _trusted_headers_user(headers)


def _trusted_headers_user(headers: Mapping[str, object]) -> ManagedUser:
    """Resolve reserved identity headers after a trusted credential is established."""

    actor = _trusted_user_id(headers)
    if not actor:
        raise ManagedAuthError(401, f"missing {USER_HEADER}")

    source_provider = headers.get(SOURCE_PROVIDER_HEADER) or None
    source_thread = headers.get(SOURCE_THREAD_HEADER) or None
    if source_provider is not None:
        if not str(source_provider).strip() or not _is_trusted_source_provider(
            str(source_provider)
        ):
            raise ManagedAuthError(401, f"unsupported {SOURCE_PROVIDER_HEADER}")
        if not source_thread:
            raise ManagedAuthError(401, f"missing {SOURCE_THREAD_HEADER}")
    elif source_thread is not None:
        raise ManagedAuthError(401, f"missing {SOURCE_PROVIDER_HEADER}")

    # Channel service principals are not end-user identities. Connected Slack
    # Events loop back as the linked person and therefore remain kind=person.
    is_service = str(actor) in {"mda:slack", CHANNEL_SERVICE_USER}

    user: ManagedUser = {
        "identity": str(actor),
        "permissions": [],
        "mda_user_id": str(actor),
    }
    if is_service:
        user["mda_user_kind"] = "service"
    if source_provider is not None and source_thread is not None:
        user["mda_source_provider"] = str(source_provider)
        user["mda_source_thread_id"] = str(source_thread)
        principal = str(headers.get(AGENT_AUTH_PRINCIPAL_HEADER) or "").strip()
        if principal:
            user["mda_agent_auth_principal_id"] = principal
    return user


def _resolve_managed_handoff_user(headers: Mapping[str, object]) -> ManagedUser | None:
    """Resolve reserved identity headers authenticated by an internal credential."""
    has_handoff_secret = _has_valid_handoff_secret(headers)
    if not _has_handoff_headers(headers) or not (
        has_handoff_secret or _has_server_api_key(headers)
    ):
        return None
    return _trusted_headers_user(headers)


def _has_valid_handoff_secret(headers: Mapping[str, object]) -> bool:
    expected = os.environ.get(HANDOFF_SECRET_ENV, "").strip()
    provided = str(headers.get(HANDOFF_SECRET_HEADER) or "").strip()
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _has_handoff_headers(headers: Mapping[str, object]) -> bool:
    return any(
        header in headers
        for header in (
            USER_HEADER,
            SOURCE_PROVIDER_HEADER,
            SOURCE_THREAD_HEADER,
            AGENT_AUTH_PRINCIPAL_HEADER,
        )
    )


def _reject_untrusted_handoff_headers(headers: Mapping[str, object]) -> None:
    if _has_handoff_headers(headers):
        raise ManagedAuthError(
            401, "reserved MDA identity headers require a trusted server credential"
        )


def _has_server_api_key(headers: Mapping[str, object]) -> bool:
    provided = str(headers.get(LANGSMITH_API_KEY_HEADER) or "").strip()
    if not provided:
        return False
    for env_name in (LANGSMITH_API_KEY_ENV, LANGCHAIN_API_KEY_ENV):
        expected = os.environ.get(env_name, "").strip()
        if expected and hmac.compare_digest(expected, provided):
            return True
    return False


class LangSmithAuthResponse(TypedDict, total=False):
    """Fields consumed from LangSmith's ``/auth/public`` response."""

    tenant_id: str
    user_id: str
    user_email: str


def _is_langsmith_api_key_auth(http: HttpIngress) -> bool:
    return isinstance(http, dict) and http.get("mode") == "langsmith_api_key"


async def _resolve_langsmith_api_key_user(headers: Mapping[str, object]) -> ManagedUser:
    return await asyncio.to_thread(_resolve_langsmith_api_key_user_sync, headers)


def _resolve_langsmith_api_key_user_sync(headers: Mapping[str, object]) -> ManagedUser:
    """Verify ``x-api-key`` while retaining MDA's thread/store authorization hooks."""
    api_key = str(headers.get(LANGSMITH_API_KEY_HEADER) or "").strip()
    endpoint = os.environ.get(LANGSMITH_AUTH_ENDPOINT_ENV, "").strip()
    expected_tenant = os.environ.get(LANGSMITH_TENANT_ID_ENV, "").strip()

    if os.environ.get(LOCAL_DEV_ENV) == "1" and (not endpoint or not expected_tenant):
        return _local_dev_service_user(headers)
    if not api_key:
        raise ManagedAuthError(401, "missing x-api-key")
    if not endpoint or not expected_tenant:
        raise ManagedAuthError(500, "LangSmith API-key authentication is not configured")

    verified = _verify_langsmith_api_key(headers, api_key, endpoint)
    tenant_id = verified.get("tenant_id")
    if not isinstance(tenant_id, str) or tenant_id != expected_tenant:
        raise ManagedAuthError(403, "API key tenant mismatch")
    return _langsmith_managed_user(verified, tenant_id)


def _verify_langsmith_api_key(
    headers: Mapping[str, object], api_key: str, endpoint: str
) -> LangSmithAuthResponse:
    forwarded_headers = {"x-api-key": api_key}
    if tenant_header := headers.get("x-tenant-id"):
        forwarded_headers["x-tenant-id"] = str(tenant_header)
    try:
        url = urljoin(f"{endpoint.rstrip('/')}/", "auth/public")
        parsed = urlsplit(url)
    except ValueError as error:
        raise ManagedAuthError(503, "LangSmith API-key verification unavailable") from error
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ManagedAuthError(503, "LangSmith API-key verification unavailable")
    cache_key = (url, api_key, forwarded_headers.get("x-tenant-id", ""))
    if (cached := _cached_langsmith_verification(cache_key)) is not None:
        return cached
    request = UrlRequest(  # noqa: S310 - endpoint is platform-owned configuration
        url, headers=forwarded_headers, method="GET"
    )
    status, payload = _request_langsmith_verification(request)

    if status == HTTPStatus.UNAUTHORIZED:
        raise ManagedAuthError(401, "invalid API key")
    if status == HTTPStatus.FORBIDDEN:
        raise ManagedAuthError(403, "API key is forbidden")
    if status < HTTPStatus.OK or status >= HTTPStatus.MULTIPLE_CHOICES:
        raise ManagedAuthError(503, "LangSmith API-key verification unavailable")
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManagedAuthError(
            503, "LangSmith API-key verification returned an invalid response"
        ) from error
    if not isinstance(decoded, dict):
        raise ManagedAuthError(503, "LangSmith API-key verification returned an invalid response")
    verified = cast("LangSmithAuthResponse", decoded)
    _cache_langsmith_verification(cache_key, verified)
    return verified


_LangSmithVerificationKey = tuple[str, str, str]
_LANGSMITH_VERIFICATION_TTL_SECONDS = 60.0
_LANGSMITH_VERIFICATION_CACHE_SIZE = 1000
_langsmith_verification_cache: OrderedDict[
    _LangSmithVerificationKey, tuple[float, LangSmithAuthResponse]
] = OrderedDict()
_langsmith_verification_cache_lock = threading.Lock()


def clear_langsmith_verification_cache_for_tests() -> None:
    """Drop cached LangSmith API-key verifications. Test-only."""
    with _langsmith_verification_cache_lock:
        _langsmith_verification_cache.clear()


def _cached_langsmith_verification(key: _LangSmithVerificationKey) -> LangSmithAuthResponse | None:
    with _langsmith_verification_cache_lock:
        entry = _langsmith_verification_cache.pop(key, None)
        if entry is None or entry[0] <= time.monotonic():
            return None
        _langsmith_verification_cache[key] = entry
        return entry[1]


def _cache_langsmith_verification(
    key: _LangSmithVerificationKey, verified: LangSmithAuthResponse
) -> None:
    with _langsmith_verification_cache_lock:
        _langsmith_verification_cache.pop(key, None)
        _langsmith_verification_cache[key] = (
            time.monotonic() + _LANGSMITH_VERIFICATION_TTL_SECONDS,
            verified,
        )
        if len(_langsmith_verification_cache) > _LANGSMITH_VERIFICATION_CACHE_SIZE:
            _langsmith_verification_cache.popitem(last=False)


def _request_langsmith_verification(request: UrlRequest) -> tuple[int, bytes]:
    """Verify credentials in at most three attempts with capped jittered backoff."""
    attempts = 3
    for attempt in range(attempts):
        status = 503
        payload = b""
        try:
            with urlopen(request, timeout=2.0) as response:  # noqa: S310 - platform-owned endpoint
                status = response.status
                payload = response.read()
        except HTTPError as error:
            status = error.code
            payload = b""
            error.close()
        except (OSError, URLError) as error:
            status = 503
            reason = error.reason if isinstance(error, URLError) else error
            if (
                attempt == attempts - 1
                or not isinstance(reason, OSError)
                or isinstance(reason, SSLCertVerificationError)
            ):
                raise ManagedAuthError(503, "LangSmith API-key verification unavailable") from error
        if status not in {408, 500, 502, 503, 504}:
            break
        if attempt < attempts - 1:
            time.sleep(secrets.randbelow(min(100 * 2**attempt, 200) + 1) / 1000)

    return status, payload


def _langsmith_managed_user(verified: LangSmithAuthResponse, tenant_id: str) -> ManagedUser:
    verified_user_id = verified.get("user_id")
    user_id = (
        f"langsmith:user:{verified_user_id.strip()}"
        if isinstance(verified_user_id, str) and verified_user_id.strip()
        else f"langsmith:tenant:{tenant_id}"
    )
    user: ManagedUser = {
        "identity": user_id,
        "permissions": [],
        "mda_user_id": user_id,
        "mda_user_kind": "service",
    }
    user_email = verified.get("user_email")
    if isinstance(user_email, str) and user_email.strip():
        user["mda_user_email"] = user_email.strip()
    return user


def _trusted_user_id(headers: Mapping[str, object]) -> object | None:
    """Caller id from the trusted headers, preferring the current spelling."""
    return headers.get(USER_HEADER) or None


def try_local_dev_trusted_user(
    _cfg: IdentityConfig, headers: Mapping[str, object]
) -> ManagedUser | None:
    """Return a synthetic local actor when ``MDA_LOCAL_DEV=1`` and ingress is absent."""
    if os.environ.get(LOCAL_DEV_ENV) != "1":
        return None
    if _has_valid_ingress_secret(headers):
        return None

    return _local_dev_service_user(headers)


def _local_dev_service_user(headers: Mapping[str, object]) -> ManagedUser:
    actor = _trusted_user_id(headers) or LOCAL_DEV_USER
    return {
        "identity": str(actor),
        "permissions": [],
        "mda_user_id": str(actor),
        "mda_user_kind": "service",
    }


def is_local_dev_unscoped_user(user: object) -> bool:
    """True for local/Companion service principals under ``MDA_LOCAL_DEV=1``."""
    if os.environ.get(LOCAL_DEV_ENV) != "1":
        return False
    actor = _field(user, "mda_user_id")
    if not actor:
        return False
    user_id = str(actor)
    return (
        user_id in (LOCAL_DEV_USER, "mda:companion") or _field(user, "mda_user_kind") == "service"
    )


def _require_ingress_secret(headers: Mapping[str, object]) -> None:
    if not _has_valid_ingress_secret(headers):
        raise ManagedAuthError(401, "invalid or missing ingress secret")


def _has_valid_ingress_secret(headers: Mapping[str, object]) -> bool:
    expected = os.environ.get(INGRESS_SECRET_ENV)
    provided = headers.get(INGRESS_SECRET_HEADER)
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


def _thread_owner_key(user: object) -> str:
    """Derive the owner value ``@auth.on.threads`` filters on.

    A managed channel thread is owned by its source conversation; every other
    caller owns its threads by user id. Channel threads are addressed by
    ``source_thread_key`` and the deliveries that reach one can legitimately
    carry different principals: the Trigger Server does not attach
    ``identity_context`` to every envelope, and any participant may reply in a
    shared thread or answer an interrupt someone else raised. Keying such a
    thread on the delivering principal denies every later delivery that does
    not match.

    Only managed ingress sets both source stamps, and only over a trusted
    loopback credential — a client presenting them is rejected in
    :func:`_reject_untrusted_handoff_headers`. ``mda_user_id`` still carries the
    principal, so credential resolution and store scoping stay per caller.
    """
    conversation = _channel_conversation_owner_key(user)
    if conversation is not None:
        return conversation
    return _runtime_user_id(user) or ""


def _channel_conversation_owner_key(user: object) -> str | None:
    """Owner key for a channel ingress caller, or ``None`` for anyone else.

    The prefix keeps conversation owners in a namespace no ``mda_user_id`` can
    reach (those are UUIDs, emails, or ``mda:*`` service ids), so a source
    thread key can never collide with a caller.
    """
    provider = _trimmed_stamp(_field(user, "mda_source_provider"))
    thread_key = _trimmed_stamp(_field(user, "mda_source_thread_id"))
    if not provider or not thread_key:
        return None
    return f"{CHANNEL_THREAD_OWNER_PREFIX}{provider}:{thread_key}"


def _trimmed_stamp(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


#: LangGraph Store namespace prefix for Slack ↔ actor account links.
IDENTITY_LINKS_STORE_PREFIX = "mda_identity_links"

#: LangGraph Store namespace prefix for legacy Connect-with-X GitHub OAuth tokens.
#:
#: The Connect flow no longer writes here, but existing deployments may still
#: hold tokens under this prefix. Keep it reserved (service principal only)
#: until contents are explicitly scrubbed — do not reopen it to ordinary Store
#: API callers.
GITHUB_CREDENTIALS_STORE_PREFIX = "mda_github_credentials"

#: Trusted service principal for ``IDENTITY_LINKS_STORE_PREFIX`` Store ops.
#: Stamped by the managed link client together with the ingress secret.
IDENTITY_LINKS_SERVICE_USER = "mda:identity-links"


def _assert_store_access(user: BaseUser, value: Mapping[str, object]) -> None:
    """Fail-closed (403) on the managed reserved namespaces.

    ``mda_identity_links`` and ``mda_github_credentials`` are deployment-wide
    managed tables — account links and (legacy) OAuth tokens for every caller —
    so only the trusted identity-links service principal may touch them. This
    check deliberately does not consult memory: it used to sit behind a
    per-caller memory check, which left the tables open to any authenticated
    caller on deployments without a user slice.

    There is no per-caller store partition to enforce beyond that, because no
    declaration mounts per-caller state in this release.

    Studio and local-dev principals are deliberately **not** exempt here. Studio
    identity is reachable by any holder of a workspace LangSmith API key (via the
    ``x-auth-scheme: langsmith`` ingress the Agent Server honours), and nothing
    legitimately reads these tables that way: the managed link client stamps
    :data:`IDENTITY_LINKS_SERVICE_USER` behind the handoff/ingress secret.
    """
    user_id = _field(user, "mda_user_id")
    raw = value.get("namespace") if isinstance(value, Mapping) else None
    namespace = [str(segment) for segment in raw] if isinstance(raw, (list, tuple)) else []
    # Managed account-link / legacy GitHub-credential tables are deployment-wide.
    # Only the trusted identity-links service principal may read/write them.
    if namespace and namespace[0] in {
        IDENTITY_LINKS_STORE_PREFIX,
        GITHUB_CREDENTIALS_STORE_PREFIX,
    }:
        if user_id == IDENTITY_LINKS_SERVICE_USER:
            return
        raise ManagedAuthError(403, "store namespace is outside the caller's identity boundary")


def _normalize_headers(headers: HeadersLike) -> dict[str, str]:
    """Return a case-insensitive header dict from a mapping or item iterable."""
    if headers is None:
        return {}
    items: Iterable[tuple[object, object]]
    if isinstance(headers, Mapping) or hasattr(headers, "items"):
        items = cast("Mapping[object, object]", headers).items()
    else:
        items = headers
    out: dict[str, str] = {}
    for key, value in items:
        key_str = key.decode() if isinstance(key, bytes) else str(key)
        if isinstance(value, bytes):
            val = value.decode()
        elif value is None:
            val = ""
        else:
            val = str(value)
        out[key_str.lower()] = val
    return out


def _http_exception(err: ManagedAuthError) -> Exception:
    """Agent Server keeps only 401/403 from SDK exceptions, so 503 uses Starlette."""
    if err.status == HTTPStatus.SERVICE_UNAVAILABLE:
        starlette_exceptions = import_module("starlette.exceptions")
        return starlette_exceptions.HTTPException(status_code=err.status, detail=err.detail)
    return Auth.exceptions.HTTPException(status_code=err.status, detail=err.detail)
