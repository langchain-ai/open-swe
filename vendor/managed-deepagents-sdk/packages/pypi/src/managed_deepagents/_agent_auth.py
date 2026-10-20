"""Agent Auth client used by ``connections.get`` and the pre-run credential gate.

Talks to the shipped Agent Auth APIs (credentials + oauth-authorization-sessions),
not the aspirational ``credentials/mint`` endpoint.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

from managed_deepagents._agent_auth_http import (
    AGENT_AUTH_TIMEOUT_SECONDS,
    ConnectionsNotConfiguredError,
    CredentialStoreError,
    agent_auth_credential_query,
    agent_auth_headers,
    missing_workspace_connection_error,
    request_agent_auth_json,
)

#: How a missing workspace is named in errors: deployments get it as
#: ``LANGSMITH_TENANT_ID``, everything else should set ``LANGSMITH_WORKSPACE_ID``.
WORKSPACE_SETTING = "LANGSMITH_WORKSPACE_ID (or LANGSMITH_TENANT_ID)"

AGENT_AUTH_PROVIDER_PAGE_SIZE = 100
AGENT_AUTH_MAX_PROVIDER_PAGES = 20

_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409

__all__ = [
    "AGENT_AUTH_TIMEOUT_SECONDS",
    "AgentAuth",
    "AgentAuthContext",
    "AgentAuthUnknownProviderError",
    "CredentialAuthorizationRequiredError",
    "PendingAuthorization",
    "ResolvePrincipalIdentity",
    "WorkspaceConnection",
    "clear_principal_resolve_cache_for_tests",
]


@dataclass(frozen=True)
class AgentAuthContext:
    base_url: str
    #: Authenticated caller, used as ``owner_id`` for user credentials.
    principal_id: str
    #: Deployment id when present. Required for agent-owned lookups; optional for user OAuth.
    agent_id: str | None = None
    api_key: str | None = None
    workspace_id: str | None = None


class ResolvePrincipalIdentity(TypedDict):
    provider: str
    issuer: NotRequired[str | None]
    subject: str


_principal_resolve_cache: dict[str, str] = {}


def clear_principal_resolve_cache_for_tests() -> None:
    """Drop cached principal resolutions. Test-only."""
    _principal_resolve_cache.clear()


def _principal_resolve_cache_key(
    context: AgentAuthContext, identity: ResolvePrincipalIdentity
) -> str:
    origin = context.base_url.rstrip("/")
    workspace = (context.workspace_id or "").strip()
    return (
        f"{origin}\0{workspace}\0"
        f"{identity['provider']}\0{identity.get('issuer') or ''}\0{identity['subject']}"
    )


class PendingAuthorization(TypedDict):
    """One connection slug the caller has not granted, with its connect URL."""

    slug: str
    connect_url: str
    auth_id: str


@dataclass(frozen=True)
class WorkspaceConnection:
    connection_id: str
    kind: str
    owner_policy: str


class CredentialAuthorizationRequiredError(RuntimeError):
    def __init__(self, slugs: list[str]) -> None:
        super().__init__(
            f"the caller has not connected: {', '.join(slugs)}. "
            "The authorization interrupt resumed without a usable credential."
        )
        self.slugs = slugs


class AgentAuthUnknownProviderError(RuntimeError):
    def __init__(self, slug: str) -> None:
        super().__init__(
            f'Agent Auth has no OAuth provider registered for slug "{slug}". '
            "Register the provider or fix the credential slug."
        )
        self.slug = slug


class AgentAuthRequest(Protocol):
    """HTTP surface bound onto :class:`AgentAuth` (method, path, optional JSON body)."""

    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class AgentAuth:
    """Bound Agent Auth HTTP client. Headers, origin, and timeout live on the instance."""

    def __init__(
        self,
        context: AgentAuthContext,
        *,
        request: AgentAuthRequest | None = None,
        timeout: float = AGENT_AUTH_TIMEOUT_SECONDS,
    ) -> None:
        """Bind this client to a caller.

        Args:
            context: Caller/run identity and Agent Auth origin.
            request: Testing only. Substitute for the default HTTP transport.
            timeout: Testing only. Override the request budget in seconds.
        """
        self.context = context
        self._timeout = timeout
        self._request: AgentAuthRequest = request if request is not None else self._urlopen_json

    def resolve_principal(self, identity: ResolvePrincipalIdentity) -> str:
        """Resolve a verified external identity to an Agent Auth principal UUID."""
        provider = identity["provider"].strip()
        raw_issuer = identity.get("issuer")
        issuer = raw_issuer.strip() if raw_issuer is not None else None
        subject = identity["subject"].strip()
        if not provider or not subject:
            raise CredentialStoreError("Agent Auth principal resolve requires provider and subject")
        if issuer == "":
            raise CredentialStoreError(
                "Agent Auth principal resolve issuer must be nonempty when supplied"
            )
        normalized: ResolvePrincipalIdentity = {"provider": provider, "subject": subject}
        if issuer is not None:
            normalized["issuer"] = issuer
        key = _principal_resolve_cache_key(self.context, normalized)
        cached = _principal_resolve_cache.get(key)
        if cached is not None:
            return cached
        principal_id = self._resolve_principal_uncached(normalized)
        _principal_resolve_cache[key] = principal_id
        return principal_id

    def _resolve_principal_uncached(self, identity: ResolvePrincipalIdentity) -> str:
        status, body = self._request(
            "POST",
            "/v1/agent-auth/principals",
            body=dict(identity),
        )
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError("Agent Auth resolve principal failed", status, body)
        principal = body.get("principal")
        principal_id = ""
        if isinstance(principal, Mapping):
            raw = principal.get("id")
            if isinstance(raw, str):
                principal_id = raw.strip()
        if not principal_id:
            raise CredentialStoreError("Agent Auth resolve principal returned no principal id")
        return principal_id

    def find_workspace_connection(self, slug: str) -> WorkspaceConnection | None:
        """Look up the workspace connection row for ``slug``, if any."""
        if not (self.context.workspace_id or "").strip():
            raise ConnectionsNotConfiguredError(WORKSPACE_SETTING)
        status, body = self._request(
            "GET",
            f"/v1/agent-auth/connections?{urllib.parse.urlencode({'slug': slug})}",
        )
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError("Agent Auth list connections failed", status, body)
        items = _connection_items(body)
        for item in items:
            if item.get("slug") == slug:
                return _workspace_connection_row(item)
        # Older Agent Auth ignores ``slug`` and returns the first page of all rows.
        if items:
            return self._page_workspace_connection(slug)
        return None

    def _page_workspace_connection(self, slug: str) -> WorkspaceConnection | None:
        """Page through all workspace connections for the row matching ``slug``."""
        cursor: str | None = None
        for _ in range(AGENT_AUTH_MAX_PROVIDER_PAGES):
            query: dict[str, str] = {
                "page_size": str(AGENT_AUTH_PROVIDER_PAGE_SIZE),
            }
            if cursor:
                query["cursor"] = cursor
            status, body = self._request(
                "GET",
                f"/v1/agent-auth/connections?{urllib.parse.urlencode(query)}",
            )
            if status >= _HTTP_BAD_REQUEST:
                raise CredentialStoreError("Agent Auth list connections failed", status, body)
            for item in _connection_items(body):
                if item.get("slug") == slug:
                    return _workspace_connection_row(item)
            next_cursor = body.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                return None
            cursor = next_cursor
        raise CredentialStoreError("Agent Auth connection list returned too many pages")

    def find_workspace_credential_id(self, slug: str) -> str | None:
        """Workspace credential id for an agent-owned opaque secret grant."""
        connection = self.find_workspace_connection(slug)
        if connection is None:
            return None
        return self.find_secret_credential_id(connection, slug)

    def find_secret_credential_id(self, connection: WorkspaceConnection, slug: str) -> str | None:
        """Resolve the credential id for a secret connection row.

        Agent-owned connection declarations scope the credential to this
        deployment. Workspace-owned credentials are deliberately not visible
        to deployments.
        """
        if connection.kind != "secret":
            return None
        agent_id = (self.context.agent_id or "").strip()
        if not agent_id:
            raise CredentialStoreError(
                "connections are not configured: LANGSMITH_HOST_PROJECT_ID is not set"
            )
        from_agent = self._credential_id_for_owner(connection.connection_id, "agent", agent_id)
        if from_agent is not None:
            return from_agent
        return self.find_credential_id(
            owner_type="agent",
            owner_id=agent_id,
            kind="secret",
            slug=slug,
        )

    def _credential_id_for_owner(
        self, connection_id: str, owner_type: str, owner_id: str
    ) -> str | None:
        """Read credential_id from GET /connections/{id}?credential_owner_*.

        Deployments authenticate with ``LANGSMITH_API_KEY`` (the minted
        service-account key). User and workspace owners additionally require
        workspace management permission.
        """
        query = urllib.parse.urlencode(
            {
                "credential_owner_type": owner_type,
                "credential_owner_id": owner_id,
            }
        )
        status, body = self._request(
            "GET",
            f"/v1/agent-auth/connections/{_path_segment(connection_id)}?{query}",
        )
        if status == _HTTP_NOT_FOUND:
            return None
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError(
                "Agent Auth get connection for owner failed"
                + (" (forbidden)" if status == _HTTP_FORBIDDEN else ""),
                status,
                body,
            )
        return _connection_credential_id(body)

    def read_user_secret(self, slug: str) -> str | None:
        """Read a caller-owned opaque secret grant for a workspace connection slug."""
        connection = self.find_workspace_connection(slug)
        if connection is None:
            return None
        return self.read_user_secret_for_connection(connection)

    def read_user_secret_for_connection(self, connection: WorkspaceConnection) -> str | None:
        """Read a caller-owned opaque secret when the connection row is already loaded."""
        if connection.kind != "secret":
            return None
        credential_id = self._credential_id_for_owner(
            connection.connection_id, "user", self.context.principal_id
        )
        if credential_id is None:
            return None
        return self.read_secret(credential_id)

    def read_agent_access_token_for_connection(self, connection: WorkspaceConnection) -> str | None:
        """Read this deployment's OAuth grant for a workspace connection."""
        agent_id = (self.context.agent_id or "").strip()
        if not agent_id:
            raise CredentialStoreError(
                "connections are not configured: LANGSMITH_HOST_PROJECT_ID is not set"
            )
        return self._read_access_token_for_owner(connection, "agent", agent_id)

    def read_user_access_token(self, slug: str) -> str | None:
        """Read the caller's OAuth access token without starting a session.

        Reads never mint, so a caller can be probed repeatedly — on every replay
        of an interrupted node, for instance — without leaving authorization
        sessions behind.

        Returns:
            The access token, or ``None`` when the caller must connect.
        """
        connection = self.find_workspace_connection(slug)
        if connection is None:
            return None
        return self.read_user_access_token_for_connection(connection)

    def read_user_access_token_for_connection(self, connection: WorkspaceConnection) -> str | None:
        """Read a caller OAuth token when the connection row is already loaded."""
        return self._read_access_token_for_owner(connection, "user", self.context.principal_id)

    def _read_access_token_for_owner(
        self,
        connection: WorkspaceConnection,
        owner_type: str,
        owner_id: str,
    ) -> str | None:
        if connection.kind != "oauth2":
            return None
        credential_id = self._credential_id_for_owner(
            connection.connection_id, owner_type, owner_id
        )
        if credential_id is None:
            return None
        return self._fetch_access_token(credential_id)

    def start_authorization_session(self, slug: str) -> PendingAuthorization:
        """Start an OAuth session for a slug the caller has not granted."""
        connection = self.find_workspace_connection(slug)
        if connection is None:
            raise missing_workspace_connection_error(slug)
        if connection.kind != "oauth2":
            raise CredentialStoreError(f"connection {slug!r} is not an OAuth connection")
        status, body = self._request(
            "POST",
            "/v1/agent-auth/oauth-authorization-sessions",
            body={
                "connection_id": connection.connection_id,
                "owner_type": "user",
                "owner_id": self.context.principal_id,
            },
        )
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError(
                "Agent Auth create oauth-authorization-session failed", status, body
            )
        auth_id = str(body.get("id") or "")
        connect_url = str(body.get("verification_url") or "")
        if not auth_id or not connect_url:
            raise CredentialStoreError(
                "Agent Auth oauth-authorization-session missing id or verification_url"
            )
        return {"slug": slug, "connect_url": connect_url, "auth_id": auth_id}

    def resolve_oauth_provider_id(self, slug: str) -> str:
        """oauth-providers has no slug filter, so the match is made across pages."""
        cursor: str | None = None
        for _ in range(AGENT_AUTH_MAX_PROVIDER_PAGES):
            query = {"page_size": str(AGENT_AUTH_PROVIDER_PAGE_SIZE)}
            if cursor:
                query["cursor"] = cursor
            status, body = self._request(
                "GET", f"/v1/agent-auth/oauth-providers?{urllib.parse.urlencode(query)}"
            )
            if status >= _HTTP_BAD_REQUEST:
                raise CredentialStoreError("Agent Auth list oauth-providers failed", status, body)
            raw_items = body.get("items")
            for item in raw_items if isinstance(raw_items, list) else []:
                if isinstance(item, Mapping) and item.get("slug") == slug and item.get("id"):
                    return str(item["id"])
            next_cursor = body.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise AgentAuthUnknownProviderError(slug)
            cursor = next_cursor
        raise CredentialStoreError("Agent Auth oauth-provider list returned too many pages")

    def find_credential_id(
        self,
        *,
        owner_type: str,
        owner_id: str,
        kind: str,
        slug: str,
    ) -> str | None:
        """Find a credential id by slug.

        The shipped credentials list filters by owner and kind only, so the
        slug match is made client-side across pages. The credential key is
        ``credential_id``, never ``id``.
        """
        cursor: str | None = None
        for _ in range(AGENT_AUTH_MAX_PROVIDER_PAGES):
            query = agent_auth_credential_query(
                owner_type=owner_type,
                owner_id=owner_id,
                kind=kind,
                page_size=AGENT_AUTH_PROVIDER_PAGE_SIZE,
                cursor=cursor,
            )
            status, body = self._request("GET", f"/v1/agent-auth/credentials?{query}")
            if status >= _HTTP_BAD_REQUEST:
                raise CredentialStoreError("Agent Auth list credentials failed", status, body)
            raw_items = body.get("items")
            items = raw_items if isinstance(raw_items, list) else []
            for item in items:
                if not isinstance(item, Mapping) or item.get("slug") != slug:
                    continue
                credential_id = item.get("credential_id")
                if isinstance(credential_id, str) and credential_id:
                    return credential_id
            next_cursor = body.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                return None
            cursor = next_cursor
        raise CredentialStoreError("Agent Auth credential list returned too many pages")

    def read_secret(self, credential_id: str) -> str | None:
        status, body = self._request(
            "GET",
            f"/v1/agent-auth/credentials/{_path_segment(credential_id)}/secret",
        )
        if status == _HTTP_NOT_FOUND:
            return None
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError("Agent Auth get secret failed", status, body)
        secret = body.get("secret")
        return secret if isinstance(secret, str) and secret else None

    def _fetch_access_token(self, credential_id: str) -> str | None:
        status, body = self._request(
            "GET",
            f"/v1/agent-auth/credentials/{_path_segment(credential_id)}/oauth2",
        )
        if _is_authorization_required(status, body):
            return None
        if status >= _HTTP_BAD_REQUEST:
            raise CredentialStoreError("Agent Auth get oauth2 failed", status, body)
        oauth2 = body.get("oauth2") if isinstance(body.get("oauth2"), dict) else body
        token = oauth2.get("access_token") if isinstance(oauth2, dict) else None
        return token if isinstance(token, str) and token else None

    def _urlopen_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self.context.base_url.startswith(("http://", "https://")):
            raise ValueError("Agent Auth URL must be an http(s) URL")
        return request_agent_auth_json(
            method,
            f"{self.context.base_url}{path}",
            body=body,
            headers=self._headers(),
            timeout=self._timeout,
        )

    def _headers(self) -> dict[str, str]:
        return agent_auth_headers(
            api_key=self.context.api_key,
            workspace_id=self.context.workspace_id,
        )


def _path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _connection_items(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Object rows of a connection list response."""
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, Mapping)]


def _workspace_connection_row(item: Mapping[str, object]) -> WorkspaceConnection:
    connection_id = item.get("id") or item.get("connection_id") or item.get("credential_id")
    kind = _connection_credential_kind(item)
    owner_policy = item.get("owner_policy")
    if not isinstance(connection_id, str) or not connection_id or not kind:
        raise CredentialStoreError("Agent Auth connection row missing id or kind")
    return WorkspaceConnection(
        connection_id=connection_id,
        kind=kind,
        owner_policy=owner_policy if isinstance(owner_policy, str) else "",
    )


def _connection_credential_kind(item: Mapping[str, object]) -> str:
    credential = item.get("credential")
    if isinstance(credential, Mapping):
        kind = credential.get("kind")
        if isinstance(kind, str) and kind:
            return kind
    kind = item.get("kind")
    return kind if isinstance(kind, str) else ""


def _connection_credential_id(body: Mapping[str, object]) -> str | None:
    credential = body.get("credential")
    if not isinstance(credential, Mapping):
        return None
    credential_id = credential.get("credential_id")
    return credential_id if isinstance(credential_id, str) and credential_id else None


def _is_authorization_required(status: int, body: dict[str, object]) -> bool:
    """Agent Auth answers 409 when a stored grant has lapsed and needs re-auth."""
    if status != _HTTP_CONFLICT:
        return False
    detail = str(body.get("detail") or body.get("title") or body.get("error") or "").lower()
    return (
        body.get("error") == "authorization_required"
        or "authorization is required" in detail
        or "oauth authorization" in detail
    )
