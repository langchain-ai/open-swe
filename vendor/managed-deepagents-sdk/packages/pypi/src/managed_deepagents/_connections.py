"""``connections.get(...)`` — read a managed connection from a tool.

``{ type: "agent" }`` resolves material owned by the current deployment.
``{ type: "user" }`` resolves per-caller grants (OAuth or opaque). Resolution
reads Agent Auth on every await and never caches material.

``mda dev`` substitutes local values for agent opaque secrets via ``MDA_DEV_*``.
User-owned connections talk to Agent Auth in every environment (including
``mda dev``) after resolving the caller's Agent Auth principal. Missing grants
interrupt at the point of access, including calls from custom tools and
middleware.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.parse
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict, TypeGuard

from managed_deepagents._agent_auth import (
    WORKSPACE_SETTING,
    AgentAuth,
    AgentAuthContext,
    CredentialAuthorizationRequiredError,
    ResolvePrincipalIdentity,
    WorkspaceConnection,
    clear_principal_resolve_cache_for_tests,
)
from managed_deepagents._agent_auth_http import (
    AGENT_AUTH_TIMEOUT_SECONDS,
    ConnectionsNotConfiguredError,
    CredentialStoreError,
    agent_auth_headers,
)
from managed_deepagents._channels.reserved_connections import (
    reserved_agent_connection_slug,
    reserved_user_connection_error,
)
from managed_deepagents._channels.source_providers import is_trusted_source_provider
from managed_deepagents._identity_runtime import (
    build_runtime_identity,
    resolve_runtime_configurable,
)
from managed_deepagents._managed_thread import on_channel_thread

if TYPE_CHECKING:
    from managed_deepagents.identity import RuntimeIdentity

__all__ = [
    "AGENT_AUTH_TIMEOUT_SECONDS",
    "ConnectionNotFoundError",
    "ConnectionOwner",
    "ConnectionReference",
    "ConnectionRequest",
    "CredentialAuthorizationRequiredError",
    "CredentialStoreError",
    "InvalidAgentAuthEndpointError",
    "agent_connection_env_key",
    "clear_principal_resolve_cache_for_tests",
    "connections",
    "is_connection_reference",
    "resolve_agent_auth_base_url",
    "resolve_agent_auth_context",
    "resolve_credential_principal_id",
    "resolve_owner_id",
]

ConnectionOwnerType = Literal["agent", "user"]


class AgentConnectionOwner(TypedDict):
    type: Literal["agent"]


class UserConnectionOwner(TypedDict, total=False):
    type: Literal["user"]
    id: str


ConnectionOwner = AgentConnectionOwner | UserConnectionOwner


class ConnectionRequest(TypedDict):
    """The resolved lookup key a store answers."""

    slug: str
    owner: ConnectionOwner
    identity: RuntimeIdentity | None


class ConnectionReference:
    """A declarative connection handle that resolves when awaited inside a run."""

    def __init__(self, slug: str, owner: ConnectionOwner, accessor: _Connections) -> None:
        self.slug = slug
        self.owner = owner
        self._accessor = accessor

    def __await__(self) -> Generator[object, None, str]:
        return self._accessor.resolve(self.slug, self.owner).__await__()


def is_connection_reference(value: object) -> TypeGuard[ConnectionReference]:
    return isinstance(value, ConnectionReference)


class ConnectionNotFoundError(LookupError):
    """No connection is set for the slug under the owner."""

    def __init__(self, slug: str, owner_type: ConnectionOwnerType) -> None:
        self.slug = slug
        self.owner_type = owner_type
        super().__init__(f"no {owner_type} connection is set for slug {slug!r}")


#: Allowlisted slug shape, so one slug always addresses one connection.
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
_MAX_SLUG_LENGTH = 64
_DEV_CREDENTIAL_PREFIX = "MDA_DEV_"
_TRUSTED_BACKEND_PROVIDER = "trusted_backend"


#: Env var naming the LangSmith platform API that serves Agent Auth.
PLATFORM_API_URL_ENV = "LANGSMITH_ENDPOINT"
_DEFAULT_LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"

#: Env var holding the deployment id, which is the agent owner's ``owner_id``.
DEPLOYMENT_ID_ENV = "LANGSMITH_HOST_PROJECT_ID"
_API_KEY_ENV = "LANGSMITH_API_KEY"
_LANGCHAIN_API_KEY_ENV = "LANGCHAIN_API_KEY"
_WORKSPACE_ID_ENV = "LANGSMITH_WORKSPACE_ID"
#: The workspace a hosted LangSmith deployment runs in. The platform sets it on
#: every revision; ``LANGSMITH_WORKSPACE_ID`` is what users should set.
_TENANT_ID_ENV = "LANGSMITH_TENANT_ID"
_LOCAL_DEV_USER = "mda:local-dev"
_LOCAL_DEV_PRINCIPAL_ENV = "MDA_AGENT_AUTH_PRINCIPAL_ID"


class InvalidAgentAuthEndpointError(CredentialStoreError):
    """``LANGSMITH_ENDPOINT`` is not a usable Agent Auth origin."""

    def __init__(self) -> None:
        super().__init__(
            f"LangSmith Agent Auth endpoint is not a valid URL (set {PLATFORM_API_URL_ENV})"
        )


class ConnectionHttpResponse(TypedDict):
    status: int
    body: bytes


#: Injectable transport: ``(url, headers, timeout) -> response``.
ConnectionHttpGet = Callable[[str, Mapping[str, str], float], ConnectionHttpResponse]


@dataclass(frozen=True)
class _AgentAuthConfig:
    base_url: str
    api_key: str
    deployment_id: str | None
    workspace_id: str
    local_dev_principal_id: str | None


class AgentAuthConnectionStore:
    """Store backed by connection rows and deployment/user grants in Agent Auth."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_get: ConnectionHttpGet | None = None,
        timeout: float = AGENT_AUTH_TIMEOUT_SECONDS,
    ) -> None:
        self._env = env
        self._http_get = http_get
        self._timeout = timeout

    async def fetch(self, request: ConnectionRequest) -> str | None:
        owner = request["owner"]
        resolved = os.environ if self._env is None else self._env
        if resolved.get("MDA_LOCAL_DEV") == "1" and owner["type"] == "agent":
            return _fetch_local_dev_agent_connection(resolved, request["slug"])
        config = _resolve_agent_auth_config(self._env)
        if owner["type"] == "agent" and not config.deployment_id:
            raise ConnectionsNotConfiguredError(DEPLOYMENT_ID_ENV)
        # Source-identity match still uses JWT sub / channel actor.
        resolve_owner_id(owner, request["identity"], config.deployment_id)
        if owner["type"] == "user":
            principal_id = await asyncio.to_thread(
                resolve_credential_principal_id,
                request["identity"],
                config,
                request_fn=self._bind_request(config),
                timeout=self._timeout,
            )
        else:
            principal_id = resolve_owner_id(owner, request["identity"], config.deployment_id)
        client = AgentAuth(
            AgentAuthContext(
                base_url=config.base_url,
                agent_id=config.deployment_id,
                principal_id=principal_id,
                api_key=config.api_key,
                workspace_id=config.workspace_id,
            ),
            request=self._bind_request(config),
            timeout=self._timeout,
        )
        slug = request["slug"]
        if owner["type"] == "agent":
            slug = reserved_agent_connection_slug(slug, config.deployment_id)
        return await asyncio.to_thread(_lookup_workspace_connection, client, slug, owner)

    def _bind_request(
        self, config: _AgentAuthConfig
    ) -> Callable[..., tuple[int, dict[str, object]]] | None:
        if self._http_get is None:
            return None
        http_get = self._http_get
        timeout = self._timeout

        def request(
            method: str,
            path: str,
            *,
            body: dict[str, object] | None = None,
        ) -> tuple[int, dict[str, object]]:
            url = f"{config.base_url}{path}"
            headers = agent_auth_headers(
                api_key=config.api_key,
                workspace_id=config.workspace_id,
            )
            # Test injects only support GET-shaped responses; path still distinguishes
            # principals vs connections for mock routers.
            del method, body
            response = http_get(url, headers, timeout)
            status = response.get("status")
            raw_body = response.get("body", b"")
            try:
                decoded = json.loads(raw_body)
            except (TypeError, ValueError):
                decoded = {}
            payload = decoded if isinstance(decoded, dict) else {}
            return int(status) if isinstance(status, int) else 0, payload

        return request


def _fetch_local_dev_agent_connection(resolved: Mapping[str, str], slug: str) -> str:
    value = resolved.get(agent_connection_env_key(slug))
    if value is None:
        value = resolved.get(_legacy_agent_connection_env_key(slug))
    if value is not None and value.strip():
        return value
    raise CredentialStoreError(
        f"local agent connection {slug!r} is not configured; set {agent_connection_env_key(slug)}"
    )


def _lookup_workspace_connection(
    client: AgentAuth,
    slug: str,
    owner: ConnectionOwner,
) -> str | None:
    if owner["type"] == "user":
        from managed_deepagents._connection_authorization import (  # noqa: PLC0415
            resolve_user_connection_grants,
        )

        return resolve_user_connection_grants(client, [slug])[slug]
    row = client.find_workspace_connection(slug)
    if row is None:
        return None
    return _lookup_agent_connection_material(client, slug, row)


def _lookup_agent_connection_material(
    client: AgentAuth,
    slug: str,
    row: WorkspaceConnection,
) -> str | None:
    if row.kind == "secret":
        credential_id = client.find_secret_credential_id(row, slug)
        if credential_id is None:
            return None
        return client.read_secret(credential_id)
    if row.kind == "oauth2":
        return client.read_agent_access_token_for_connection(row)
    return None


class _Connections:
    """The type of the module-level ``connections`` accessor."""

    def __init__(self, store: AgentAuthConnectionStore | None = None) -> None:
        self._store = AgentAuthConnectionStore() if store is None else store

    def get(
        self,
        slug: str,
        owner: ConnectionOwner | Mapping[str, object],
    ) -> ConnectionReference:
        """Describe a connection lookup; await the handle to read its value."""
        normalized_slug = _normalize_slug(slug)
        normalized_owner = _normalize_owner(owner)
        if normalized_owner["type"] == "user":
            unsupported = reserved_user_connection_error(normalized_slug)
            if unsupported is not None:
                raise ValueError(unsupported)
        return ConnectionReference(normalized_slug, normalized_owner, self)

    async def resolve(self, slug: str, owner: ConnectionOwner) -> str:
        configurable = resolve_runtime_configurable(None)
        # Agent-owned material belongs to the deployment, not to a caller, so it
        # resolves outside a run too, e.g. in a channel callback.
        if configurable is None and owner["type"] == "agent":
            return await self.resolve_deployment_secret(slug)
        if configurable is None:
            raise RuntimeError(
                "connections.get(...) must be awaited inside a run: resolve a connection in a "
                "tool or middleware, not at import time"
            )
        request: ConnectionRequest = {
            "slug": slug,
            "owner": owner,
            "identity": build_runtime_identity(configurable),
        }
        value = await self._store.fetch(request)
        if value is None:
            if owner["type"] == "user":
                raise CredentialAuthorizationRequiredError([slug])
            raise ConnectionNotFoundError(slug, owner["type"])
        return value

    async def resolve_deployment_secret(self, slug: str) -> str:
        """Read an agent-owned secret outside a run."""
        request: ConnectionRequest = {
            "slug": slug,
            "owner": {"type": "agent"},
            "identity": None,
        }
        value = await self._store.fetch(request)
        if value is None:
            raise ConnectionNotFoundError(slug, "agent")
        return value

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"connections(store={type(self._store).__name__})"


def _normalize_slug(slug: str) -> str:
    if not isinstance(slug, str):
        raise ValueError(f"connection slug must be a string, not {type(slug).__name__}")
    canonical = slug.strip()
    if (
        not canonical
        or len(canonical) > _MAX_SLUG_LENGTH
        or _SLUG_PATTERN.fullmatch(canonical) is None
    ):
        raise ValueError(
            f"connection slug {slug!r} is invalid: use lowercase letters, digits, and "
            f"single hyphens or slashes, at most {_MAX_SLUG_LENGTH} characters (e.g. 'notion-one')"
        )
    return canonical


def agent_connection_env_key(slug: str) -> str:
    """Environment variable used for one agent-owned development connection.

    ``MDA_DEV_`` and the slug uppercased, ``-`` as ``_`` and ``/`` as ``__``
    (``mda/github-token`` → ``MDA_DEV_MDA__GITHUB_TOKEN``), so shells keep it.
    """
    return _legacy_agent_connection_env_key(slug).replace("/", "__")


def _legacy_agent_connection_env_key(slug: str) -> str:
    """The earlier spelling, with ``/`` kept; Linux shells drop such names."""
    return f"{_DEV_CREDENTIAL_PREFIX}{_normalize_slug(slug).upper().replace('-', '_')}"


def _normalize_owner(owner: ConnectionOwner | Mapping[str, object]) -> ConnectionOwner:
    if not isinstance(owner, Mapping):
        raise ValueError(
            'connection owner is required: pass {"type": "agent"} or {"type": "user"}, '
            f"not {type(owner).__name__}"
        )
    unknown = sorted(key for key in owner if key not in {"type", "id"})
    if unknown:
        raise ValueError(
            f"connection owner has unknown {'keys' if len(unknown) > 1 else 'key'} "
            f"{', '.join(repr(key) for key in unknown)}; the keys are 'type' and 'id'"
        )
    owner_type = owner.get("type")
    if owner_type == "agent":
        if "id" in owner:
            raise ValueError('connection owner "id" is not accepted for {"type": "agent"}')
        return {"type": "agent"}
    if owner_type == "user":
        owner_id = owner.get("id")
        if owner_id is None:
            return {"type": "user"}
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError('connection owner "id" must be a non-empty string')
        return {"type": "user", "id": owner_id.strip()}
    raise ValueError(
        f"connection owner type {owner_type!r} is not supported; use {{'type': 'agent'}} "
        f"or {{'type': 'user'}}"
    )


def resolve_owner_id(
    owner: ConnectionOwner,
    identity: RuntimeIdentity | None,
    deployment_id: str | None,
) -> str:
    if owner["type"] == "user":
        authenticated = None
        if identity is not None:
            user_id = identity["user"].get("id")
            if isinstance(user_id, str) and user_id:
                authenticated = user_id
        if not authenticated:
            raise CredentialStoreError("user connections require an authenticated user principal")
        explicit = owner.get("id")
        if explicit and explicit != authenticated:
            raise CredentialStoreError(
                "user connection owner id must match the authenticated principal"
            )
        return authenticated
    if not deployment_id:
        raise CredentialStoreError(
            f"connections are not configured: {DEPLOYMENT_ID_ENV} is not set"
        )
    return deployment_id


def resolve_agent_auth_context(
    identity: RuntimeIdentity | None,
    env: Mapping[str, str] | None = None,
    *,
    request_fn: Callable[..., tuple[int, dict[str, object]]] | None = None,
    timeout: float = AGENT_AUTH_TIMEOUT_SECONDS,
) -> AgentAuthContext:
    config = _resolve_agent_auth_config(env)
    principal_id = resolve_credential_principal_id(
        identity, config, request_fn=request_fn, timeout=timeout
    )
    return AgentAuthContext(
        base_url=config.base_url,
        agent_id=config.deployment_id,
        principal_id=principal_id,
        api_key=config.api_key,
        workspace_id=config.workspace_id,
    )


CHANNEL_THREAD_USER_CONNECTION_ERROR = (
    "user-owned connections are unavailable when resuming a channel's "
    "conversation from outside that channel: it holds content from outside "
    "the workspace, which must not act with your grants"
)
"""Why a Studio resume of a channel's conversation gets no user connections.

The conversation holds content from outside the workspace, which must not
steer the grants of whoever resumes it.
"""


def outside_caller_on_channel_thread(identity: RuntimeIdentity | None) -> bool:
    """Whether the run continues a channel's conversation for a caller who is not that channel.

    A person resuming it from Studio is one. The channel's own runs, a linked
    Slack person's included, keep their connections.
    """
    if not on_channel_thread():
        return False
    source = (identity or {}).get("source") or {}
    provider = source.get("provider") if isinstance(source, Mapping) else None
    return not (isinstance(provider, str) and is_trusted_source_provider(provider))


_UNMAPPED_SENDER_PROVIDERS = frozenset({"github"})
"""Channels whose senders a delivery verifies but who cannot be mapped to a
user of the agent's identity: the principal ``identity.py`` resolves, from
LangSmith, Supabase or another provider. They cannot use tools that act on a
user's behalf or own user grants, and asking them to connect an account would
mean posting the link where everyone sees it."""


def unmapped_channel_provider(identity: RuntimeIdentity | None) -> str | None:
    """The channel, e.g. ``github``, whose sender the run acts for without a user.

    No user of the agent's identity is behind them: such a person has user
    memory but no user-owned connections.
    """
    if identity is None:
        return None
    user = identity.get("user") or {}
    if not isinstance(user, Mapping) or user.get("kind") != "person":
        return None
    stamped = identity.get("agent_auth_principal_id")
    if isinstance(stamped, str) and stamped.strip():
        return None
    source = identity.get("source") or {}
    provider = source.get("provider") if isinstance(source, Mapping) else None
    return provider if provider in _UNMAPPED_SENDER_PROVIDERS else None


def channel_provider(identity: RuntimeIdentity | None) -> str | None:
    """The channel a run started by a channel event runs as, e.g. ``github``.

    Such a run runs as the channel, not as the person who sent the event, so
    there is no one to own a user grant.
    """
    if identity is None:
        return None
    user = identity.get("user") or {}
    if not isinstance(user, Mapping) or user.get("kind") != "service":
        return None
    source = identity.get("source") or {}
    channel = source.get("provider") if isinstance(source, Mapping) else None
    if isinstance(channel, str) and is_trusted_source_provider(channel):
        return channel
    return None


def resolve_credential_principal_id(
    identity: RuntimeIdentity | None,
    config: _AgentAuthConfig,
    *,
    request_fn: Callable[..., tuple[int, dict[str, object]]] | None = None,
    timeout: float = AGENT_AUTH_TIMEOUT_SECONDS,
) -> str:
    """Map a runtime identity to the Agent Auth principal used for user grants."""
    if identity is None:
        raise CredentialStoreError("user connections require an authenticated user principal")
    user = identity.get("user") or {}
    user_id = user.get("id") if isinstance(user, Mapping) else None
    if not isinstance(user_id, str) or not user_id.strip():
        raise CredentialStoreError("user connections require an authenticated user principal")
    if outside_caller_on_channel_thread(identity):
        raise CredentialStoreError(CHANNEL_THREAD_USER_CONNECTION_ERROR)
    unmapped = unmapped_channel_provider(identity)
    if unmapped is not None:
        raise CredentialStoreError(
            f"user-owned connections are unavailable to {unmapped} senders: a "
            f"{unmapped} account cannot be mapped to a user of this agent's identity"
        )
    kind = user.get("kind") if isinstance(user, Mapping) else None
    if kind == "service":
        if user_id.strip() == _LOCAL_DEV_USER and config.local_dev_principal_id is not None:
            return config.local_dev_principal_id
        # A channel event (e.g. a GitHub comment) starts a run that acts as the
        # channel's app, not as the person who triggered it, so there is no one
        # to own the grant.
        channel = channel_provider(identity)
        if channel is not None:
            raise CredentialStoreError(
                "user-owned connections require a person principal; "
                f"a {channel} event starts an agent run that acts as the channel's "
                "registered app, not as the person who triggered the event"
            )
        raise CredentialStoreError(
            "user-owned connections require a person principal; "
            "service callers cannot own OAuth grants"
        )
    stamped = identity.get("agent_auth_principal_id")
    if isinstance(stamped, str) and stamped.strip():
        return stamped.strip()

    subject = user_id.strip()
    external = _external_identity_for_resolve(identity, config.workspace_id)
    if external is None:
        raise CredentialStoreError(
            "user-owned connections need a resolvable identity "
            "(validated-token issuer/subject, channel identity_context, or trusted backend)"
        )
    bootstrap = AgentAuth(
        AgentAuthContext(
            base_url=config.base_url,
            principal_id=subject,
            api_key=config.api_key,
            workspace_id=config.workspace_id,
            agent_id=config.deployment_id,
        ),
        request=request_fn,
        timeout=timeout,
    )
    return bootstrap.resolve_principal(external)


def _external_identity_for_resolve(
    identity: RuntimeIdentity,
    workspace_id: str | None,
) -> ResolvePrincipalIdentity | None:
    if identity["source"]["provider"] == "studio":
        workspace = workspace_id.strip() if workspace_id else ""
        subject = identity.get("identity_subject")
        user_id = subject.strip() if isinstance(subject, str) else ""
        if workspace and user_id and identity.get("identity_provider") == "langsmith":
            return {"provider": "langsmith", "issuer": workspace, "subject": user_id}
        return None
    provider = identity.get("identity_provider")
    issuer = identity.get("identity_issuer")
    subject = identity.get("identity_subject")
    if (
        isinstance(provider, str)
        and provider.strip()
        and isinstance(issuer, str)
        and issuer.strip()
        and isinstance(subject, str)
        and subject.strip()
    ):
        return {
            "provider": provider.strip(),
            "issuer": issuer.strip(),
            "subject": subject.strip(),
        }
    user = identity.get("user") or {}
    trusted_subject = user.get("id") if isinstance(user, Mapping) else None
    if not isinstance(trusted_subject, str) or not trusted_subject.strip():
        return None
    source_provider = identity["source"]["provider"]
    if is_trusted_source_provider(source_provider):
        return {"provider": source_provider, "subject": trusted_subject.strip()}
    workspace = (workspace_id or "").strip()
    return (
        {
            "provider": _TRUSTED_BACKEND_PROVIDER,
            "issuer": workspace,
            "subject": trusted_subject.strip(),
        }
        if workspace
        else None
    )


def _resolve_agent_auth_config(env: Mapping[str, str] | None) -> _AgentAuthConfig:
    resolved = os.environ if env is None else env
    api_key = (resolved.get(_API_KEY_ENV) or "").strip() or (
        resolved.get(_LANGCHAIN_API_KEY_ENV) or ""
    ).strip()
    if not api_key:
        raise ConnectionsNotConfiguredError(f"{_API_KEY_ENV} or {_LANGCHAIN_API_KEY_ENV}")
    workspace_id = resolve_workspace_id(resolved)
    if workspace_id is None:
        raise ConnectionsNotConfiguredError(WORKSPACE_SETTING)
    return _AgentAuthConfig(
        base_url=resolve_agent_auth_base_url(resolved),
        api_key=api_key,
        deployment_id=(resolved.get(DEPLOYMENT_ID_ENV) or "").strip() or None,
        workspace_id=workspace_id,
        local_dev_principal_id=(
            (resolved.get(_LOCAL_DEV_PRINCIPAL_ENV) or "").strip() or None
            if resolved.get("MDA_LOCAL_DEV") == "1"
            else None
        ),
    )


def resolve_workspace_id(env: Mapping[str, str] | None = None) -> str | None:
    """The LangSmith workspace Agent Auth calls are scoped to.

    Users set ``LANGSMITH_WORKSPACE_ID``, which wins. A hosted deployment
    always runs with ``LANGSMITH_TENANT_ID``, which the platform sets, so a
    deploy that never named a workspace still has one.
    """
    resolved = os.environ if env is None else env
    for name in (_WORKSPACE_ID_ENV, _TENANT_ID_ENV):
        value = (resolved.get(name) or "").strip()
        if value:
            return value
    return None


def resolve_agent_auth_base_url(env: Mapping[str, str] | None = None) -> str:
    """Origin for Agent Auth HTTP calls from ``LANGSMITH_ENDPOINT``.

    Cloud SaaS revisions omit that reserved name and inject
    ``LANGCHAIN_ENDPOINT`` instead; read it as the same setting so we do not
    default to production.
    """
    resolved = os.environ if env is None else env
    raw = (
        (resolved.get(PLATFORM_API_URL_ENV) or "").strip()
        or (resolved.get("LANGCHAIN_ENDPOINT") or "").strip()
        or _DEFAULT_LANGSMITH_ENDPOINT
    )
    try:
        parsed = urllib.parse.urlsplit(raw.rstrip("/"))
        _ = parsed.port
    except ValueError:
        raise InvalidAgentAuthEndpointError from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidAgentAuthEndpointError
    return f"{parsed.scheme}://{parsed.netloc}"


connections = _Connections()
