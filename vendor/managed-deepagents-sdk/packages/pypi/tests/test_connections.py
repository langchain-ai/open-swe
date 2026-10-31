"""Unit tests for ``connections.get``."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

import managed_deepagents._connections as connections_mod
from managed_deepagents import (
    ConnectionNotFoundError,
    CredentialStoreError,
    InvalidAgentAuthEndpointError,
    _connections,
    connections,
)
from managed_deepagents._agent_auth_http import ConnectionsNotConfiguredError
from managed_deepagents._channels.source_providers import register_declared_source_providers
from managed_deepagents._connections import (
    CHANNEL_THREAD_USER_CONNECTION_ERROR,
    AgentAuthConnectionStore,
    ConnectionHttpResponse,
    ConnectionRequest,
    _Connections,
    _fetch_local_dev_agent_connection,
    agent_connection_env_key,
    clear_principal_resolve_cache_for_tests,
    resolve_agent_auth_base_url,
    resolve_agent_auth_context,
)
from managed_deepagents._identity_runtime import build_runtime_identity


@pytest.fixture(autouse=True)
def _inside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _connections, "resolve_runtime_configurable", lambda _runtime: {"thread_id": "t-1"}
    )


_ENV = {
    "LANGSMITH_API_KEY": "ls-key",
    "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
    "LANGSMITH_WORKSPACE_ID": "workspace-1",
}

_AGENT: Mapping[str, object] = {"type": "agent"}
_USER: Mapping[str, object] = {"type": "user"}


def _ok(payload: object) -> ConnectionHttpResponse:
    return {"status": 200, "body": json.dumps(payload).encode()}


def _connection_page(
    slug: str,
    kind: str,
    owner_policy: str = "agent",
    *,
    connection_id: str | None = None,
    next_cursor: str | None = None,
) -> object:
    cid = connection_id or f"conn-{slug}"
    return {
        "items": [
            {
                "connection_id": cid,
                "slug": slug,
                "kind": kind,
                "owner_policy": owner_policy,
            }
        ],
        "next_cursor": next_cursor,
    }


def _workspace_credential_page(slug: str) -> object:
    return {"items": [{"credential_id": f"cred-{slug}", "slug": slug, "kind": "secret"}]}


def _credential_page(slug: str, kind: str = "oauth2") -> object:
    return {"items": [{"credential_id": f"cred-{slug}", "slug": slug, "kind": kind}]}


class _FakeAgentAuth:
    """Agent Auth stand-in: connections list, for-owner get, then material."""

    def __init__(
        self,
        *connection_pages: object,
        credential_pages: list[object] | None = None,
        for_owner: object | None = None,
        secret: object | None = None,
        access_token: str = "ya29.caller",
    ) -> None:
        self._connection_pages = list(connection_pages)
        self._credential_pages = list(credential_pages or [])
        self._for_owner = for_owner
        self._secret = {"secret": "tok-1"} if secret is None else secret
        self._access_token = access_token
        self._connection_i = 0
        self._credential_i = 0
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> ConnectionHttpResponse:
        del timeout
        self.calls.append((url, dict(headers)))
        if url.endswith("/oauth-authorization-sessions"):
            body = {
                "id": "sess-notion-one",
                "verification_url": "https://idp.example/notion-one",
            }
        elif url.endswith("/secret"):
            body = self._secret
        elif url.endswith("/oauth2"):
            body = {"oauth2": {"access_token": self._access_token}}
        elif url.rstrip("/").endswith("/principals"):
            body = {"principal": {"id": "aa-principal-1"}}
        elif "/connections/" in url and "credential_owner_" in url:
            body = self._next_for_owner_body()
        elif "/connections" in url:
            body = self._connection_pages[self._connection_i]
            self._connection_i += 1
        elif "/credentials?" in url:
            body = self._next_credential_page()
        else:
            raise AssertionError(f"unexpected Agent Auth URL {url!r}")
        return _ok(body)

    def _next_credential_page(self) -> object:
        page = (
            self._credential_pages[self._credential_i]
            if self._credential_i < len(self._credential_pages)
            else {"items": []}
        )
        self._credential_i += 1
        return page

    def _next_for_owner_body(self) -> object:
        if self._for_owner is not None:
            return self._for_owner
        page = self._next_credential_page()
        items = page.get("items") if isinstance(page, dict) else None
        grant = items[0] if isinstance(items, list) and items else None
        if isinstance(grant, dict) and grant.get("credential_id"):
            return {
                "id": f"conn-{grant.get('slug', 'unknown')}",
                "slug": grant.get("slug"),
                "credential": {
                    "kind": grant.get("kind", "oauth2"),
                    "credential_id": grant["credential_id"],
                },
            }
        return {
            "id": "conn-missing",
            "slug": "unknown",
            "credential": {"kind": "oauth2"},
        }


def _run(handle: Awaitable[str]) -> str:
    async def _inner() -> str:
        return await handle

    return asyncio.run(_inner())


def _store(fake: _FakeAgentAuth) -> _Connections:
    return _Connections(AgentAuthConnectionStore(env=_ENV, http_get=fake))


@pytest.mark.parametrize("slug", ["notion-one", "team/notion-one", "org/team/notion"])
def test_agent_connection_resolves_from_agent_auth(slug: str) -> None:
    fake = _FakeAgentAuth(
        _connection_page(slug, "secret", "agent", connection_id="conn-notion-one"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )

    assert _run(_store(fake).get(slug, _AGENT)) == "tok-1"

    list_url, list_headers = fake.calls[0]
    assert list_url.startswith("https://api.smith.langchain.com/v1/agent-auth/connections?")
    assert list_headers["x-api-key"] == "ls-key"
    assert list_headers["x-tenant-id"] == "workspace-1"
    grant_url = fake.calls[1][0]
    assert grant_url.startswith(
        "https://api.smith.langchain.com/v1/agent-auth/connections/conn-notion-one?"
    )
    assert "credential_owner_type=agent" in grant_url
    assert "credential_owner_id=deployment-1" in grant_url
    assert fake.calls[2][0].endswith("/v1/agent-auth/credentials/cred-notion-one/secret")


def test_agent_owner_resolves_an_oauth_grant_for_the_deployment() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "agent"),
        credential_pages=[_credential_page("notion-one")],
    )

    assert _run(_store(fake).get("notion-one", _AGENT)) == "ya29.caller"
    assert "credential_owner_type=agent" in fake.calls[1][0]
    assert "credential_owner_id=deployment-1" in fake.calls[1][0]
    assert fake.calls[2][0].endswith("/v1/agent-auth/credentials/cred-notion-one/oauth2")


def test_owner_is_required() -> None:
    untyped_get = cast("Any", connections.get)
    with pytest.raises(TypeError):
        untyped_get("notion-one")


def test_lookup_takes_one_workspace_list_request() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )

    assert _run(_store(fake).get("notion-one", _AGENT)) == "tok-1"
    assert len(fake.calls) == 3
    assert (
        sum(1 for url, _ in fake.calls if "/connections?" in url and "/connections/" not in url)
        == 1
    )
    assert "cursor" not in fake.calls[0][0]


def test_unknown_slug_raises_without_naming_the_store_or_a_value() -> None:
    fake = _FakeAgentAuth({"items": [], "next_cursor": None})

    with pytest.raises(ConnectionNotFoundError) as raised:
        _run(_store(fake).get("notion-one", _AGENT))

    assert str(raised.value) == "no agent connection is set for slug 'notion-one'"
    assert raised.value.slug == "notion-one"
    assert raised.value.owner_type == "agent"


def test_blank_secret_material_counts_as_unset() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
        secret={"secret": ""},
    )

    with pytest.raises(ConnectionNotFoundError):
        _run(_store(fake).get("notion-one", _AGENT))


def test_store_failure_carries_the_status_but_no_response_body() -> None:
    def failing(url: str, headers: Mapping[str, str], timeout: float) -> ConnectionHttpResponse:
        del url, headers, timeout
        return {"status": 500, "body": b'{"secret": "tok-1"}'}

    accessor = _Connections(AgentAuthConnectionStore(env=_ENV, http_get=failing))

    with pytest.raises(CredentialStoreError, match="Agent Auth list connections failed") as raised:
        _run(accessor.get("notion-one", _AGENT))

    assert raised.value.status == 500
    assert "tok-1" not in str(raised.value)


def test_missing_deployment_id_is_reported_by_variable_name() -> None:
    accessor = _Connections(
        AgentAuthConnectionStore(
            env={"LANGSMITH_API_KEY": "ls-key", "LANGSMITH_WORKSPACE_ID": "workspace-1"},
            http_get=_FakeAgentAuth(),
        )
    )

    with pytest.raises(ConnectionsNotConfiguredError, match="LANGSMITH_HOST_PROJECT_ID is not set"):
        _run(accessor.get("notion-one", _AGENT))


def test_missing_workspace_id_is_reported_by_variable_name() -> None:
    accessor = _Connections(
        AgentAuthConnectionStore(env={"LANGSMITH_API_KEY": "ls-key"}, http_get=_FakeAgentAuth())
    )

    with pytest.raises(
        ConnectionsNotConfiguredError,
        match=re.escape("LANGSMITH_WORKSPACE_ID (or LANGSMITH_TENANT_ID) is not set"),
    ):
        _run(accessor.get("notion-one", _AGENT))


@pytest.mark.parametrize(
    ("workspace", "expected"),
    [
        # A hosted deployment runs with the platform's LANGSMITH_TENANT_ID.
        ({"LANGSMITH_TENANT_ID": "tenant-1"}, "tenant-1"),
        # A workspace the user set wins.
        (
            {"LANGSMITH_WORKSPACE_ID": "workspace-1", "LANGSMITH_TENANT_ID": "tenant-1"},
            "workspace-1",
        ),
    ],
)
def test_workspace_falls_back_to_the_deployment_tenant(
    workspace: dict[str, str], expected: str
) -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent", connection_id="conn-notion-one"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )
    env = {
        "LANGSMITH_API_KEY": "ls-key",
        "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
        **workspace,
    }

    assert (
        _run(
            _Connections(AgentAuthConnectionStore(env=env, http_get=fake)).get("notion-one", _AGENT)
        )
        == "tok-1"
    )
    assert fake.calls[0][1]["x-tenant-id"] == expected


def test_missing_api_key_is_reported_by_variable_name() -> None:
    accessor = _Connections(AgentAuthConnectionStore(env={}, http_get=_FakeAgentAuth()))

    with pytest.raises(
        ConnectionsNotConfiguredError,
        match="LANGSMITH_API_KEY or LANGCHAIN_API_KEY is not set",
    ):
        _run(accessor.get("notion-one", _AGENT))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:pw@api.smith.langchain.com",
        "ftp://api.smith.langchain.com",
        "not a url",
        "https://api.smith.langchain.com/api/v1",
    ],
)
def test_unusable_platform_endpoint_is_rejected(endpoint: str) -> None:
    accessor = _Connections(
        AgentAuthConnectionStore(
            env={**_ENV, "LANGSMITH_ENDPOINT": endpoint}, http_get=_FakeAgentAuth()
        )
    )

    with pytest.raises(InvalidAgentAuthEndpointError):
        _run(accessor.get("notion-one", _AGENT))


def test_resolve_agent_auth_base_url_uses_langsmith_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com/")
    monkeypatch.delenv("LANGCHAIN_ENDPOINT", raising=False)
    assert resolve_agent_auth_base_url() == "https://eu.api.smith.langchain.com"


def test_resolve_agent_auth_base_url_falls_back_to_langchain_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.setenv("LANGCHAIN_ENDPOINT", "https://dev.api.smith.langchain.com/")
    assert resolve_agent_auth_base_url() == "https://dev.api.smith.langchain.com"


def test_resolve_agent_auth_base_url_defaults_to_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGCHAIN_ENDPOINT", raising=False)
    assert resolve_agent_auth_base_url() == "https://api.smith.langchain.com"


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "   ",
        "Notion-One",
        "notion_one",
        "notion--one",
        "-notion",
        "../../etc/passwd",
        "/notion",
        "notion/",
        "team//notion",
        "team/-notion",
        "team/../notion",
        "notion one",
        "notion-one=x",
        "n" * 65,
    ],
)
def test_slug_outside_the_allowlist_is_rejected(slug: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        _run(connections.get(slug, _AGENT))


def test_unsupported_owner_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="not supported"):
        _run(connections.get("notion-one", {"type": "workspace"}))


def test_agent_owner_rejects_an_id() -> None:
    with pytest.raises(ValueError, match='"id" is not accepted'):
        _run(connections.get("notion-one", {"type": "agent", "id": "a-1"}))


def _as_user(monkeypatch: pytest.MonkeyPatch, user_id: str) -> None:
    monkeypatch.setattr(
        _connections,
        "resolve_runtime_configurable",
        lambda _runtime: {
            "thread_id": "t-1",
            "langgraph_auth_user": {
                "identity": user_id,
                "permissions": [],
                "mda_user_id": user_id,
                "mda_user_kind": "person",
                "mda_agent_auth_principal_id": user_id,
            },
        },
    )


def test_user_owner_defaults_to_the_current_principal() -> None:
    reference = connections.get("notion-one", {"type": "user"})

    assert reference.owner == {"type": "user"}


def test_user_owner_rejects_a_blank_explicit_id() -> None:
    with pytest.raises(ValueError, match='"id" must be a non-empty string'):
        _run(connections.get("notion-one", {"type": "user", "id": "  "}))


def test_user_owner_resolves_an_oauth_grant_not_a_static_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "user"),
        credential_pages=[_credential_page("notion-one")],
    )

    assert _run(_store(fake).get("notion-one", _USER)) == "ya29.caller"
    assert "credential_owner_type=user" in fake.calls[1][0]
    assert "credential_owner_id=user-1" in fake.calls[1][0]
    assert "/connections/" in fake.calls[1][0]
    assert fake.calls[2][0].endswith("/v1/agent-auth/credentials/cred-notion-one/oauth2")


def test_studio_user_reads_grant_under_resolved_canonical_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_principal_resolve_cache_for_tests()
    monkeypatch.setattr(
        _connections,
        "resolve_runtime_configurable",
        lambda _runtime: {
            "langgraph_auth_user": {
                "kind": "StudioUser",
                "identity": "legacy-user",
                "ls_user_id": "canonical-user",
                "is_authenticated": True,
            },
        },
    )
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "user"),
        credential_pages=[_credential_page("notion-one")],
    )
    try:
        assert _run(_store(fake).get("notion-one", _USER)) == "ya29.caller"
        assert fake.calls[0][0].endswith("/v1/agent-auth/principals")
        assert "credential_owner_type=user" in fake.calls[2][0]
        assert "credential_owner_id=aa-principal-1" in fake.calls[2][0]
        assert fake.calls[3][0].endswith("/v1/agent-auth/credentials/cred-notion-one/oauth2")
    finally:
        clear_principal_resolve_cache_for_tests()


def test_user_owner_resolves_an_opaque_secret_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(
        _connection_page("tavily", "secret", "user"),
        credential_pages=[_credential_page("tavily", kind="secret")],
        secret={"secret": "tvly-user-key"},
    )

    assert _run(_store(fake).get("tavily", _USER)) == "tvly-user-key"
    assert "credential_owner_type=user" in fake.calls[1][0]


def test_unconnected_caller_interrupts_with_an_authorization_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "user"),
        _connection_page("notion-one", "oauth2", "user"),
        credential_pages=[{"items": []}],
    )
    interrupts: list[object] = []

    class _InterruptedError(RuntimeError):
        pass

    def interrupt(payload: object) -> None:
        interrupts.append(payload)
        raise _InterruptedError

    monkeypatch.setattr("managed_deepagents._connection_authorization.graph_interrupt", interrupt)

    with pytest.raises(_InterruptedError):
        _run(_store(fake).get("notion-one", _USER))

    assert interrupts == [
        {
            "type": "credential_authorization_required",
            "message": "Connect the following integrations to continue.",
            "credentials": [
                {
                    "slug": "notion-one",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/notion-one",
                    "auth_id": "sess-notion-one",
                }
            ],
        }
    ]


def test_explicit_user_id_must_match_the_authenticated_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(_connection_page("notion-one", "oauth2", "user"))

    with pytest.raises(CredentialStoreError, match="must match the authenticated principal"):
        _run(_store(fake).get("notion-one", {"type": "user", "id": "someone-else"}))

    assert fake.calls == []


def test_matching_explicit_user_id_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "user"),
        credential_pages=[_credential_page("notion-one")],
    )

    token = _run(_store(fake).get("notion-one", {"type": "user", "id": "user-1"}))

    assert token == "ya29.caller"
    assert "credential_owner_id=user-1" in fake.calls[1][0]


def test_user_owner_requires_workspace_id_for_hosted_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "u-1")
    accessor = _Connections(
        AgentAuthConnectionStore(env={"LANGSMITH_API_KEY": "ls-key"}, http_get=_FakeAgentAuth())
    )

    with pytest.raises(CredentialStoreError, match="LANGSMITH_WORKSPACE_ID"):
        _run(accessor.get("notion-one", _USER))


def test_agent_connection_prefers_the_normalized_development_environment() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )
    accessor = _Connections(
        AgentAuthConnectionStore(
            env={"MDA_LOCAL_DEV": "1", "MDA_DEV_NOTION_ONE": "local-token"},
            http_get=fake,
        )
    )

    assert _run(accessor.get("notion-one", _AGENT)) == "local-token"
    assert agent_connection_env_key("notion-one") == "MDA_DEV_NOTION_ONE"
    # Shells drop names with "/", so namespaces become "__".
    assert agent_connection_env_key("mda/github-token") == "MDA_DEV_MDA__GITHUB_TOKEN"
    for name in ("MDA_DEV_MDA__GITHUB_TOKEN", "MDA_DEV_MDA/GITHUB_TOKEN"):
        assert (
            _fetch_local_dev_agent_connection({name: "github-token"}, "mda/github-token")
            == "github-token"
        )
    assert fake.calls == []


def test_missing_local_agent_value_is_reported_by_variable_name() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )
    accessor = _Connections(AgentAuthConnectionStore(env={"MDA_LOCAL_DEV": "1"}, http_get=fake))

    with pytest.raises(CredentialStoreError, match="set MDA_DEV_NOTION_ONE"):
        _run(accessor.get("notion-one", _AGENT))
    assert fake.calls == []


def test_development_environment_does_not_override_hosted_agent_auth() -> None:
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )
    accessor = _Connections(
        AgentAuthConnectionStore(env={**_ENV, "MDA_DEV_NOTION_ONE": "local-token"}, http_get=fake)
    )

    assert _run(accessor.get("notion-one", _AGENT)) == "tok-1"


def test_local_user_connection_resolves_via_agent_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as_user(monkeypatch, "user-1")
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "oauth2", "user"),
        for_owner={
            "id": "conn-notion-one",
            "slug": "notion-one",
            "credential": {"kind": "oauth2", "credential_id": "cred-notion-one"},
        },
        access_token="ya29.local",
    )
    accessor = _Connections(
        AgentAuthConnectionStore(env={**_ENV, "MDA_LOCAL_DEV": "1"}, http_get=fake)
    )

    assert _run(accessor.get("notion-one", _USER)) == "ya29.local"
    assert any("/connections" in url for url, _ in fake.calls)


def test_user_get_outside_a_run_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_connections, "resolve_runtime_configurable", lambda _runtime: None)
    fake = _FakeAgentAuth(
        _connection_page("notion-one", "secret", "agent"),
        credential_pages=[_workspace_credential_page("notion-one")],
    )

    # A user's connection needs the caller's identity, which only a run has.
    with pytest.raises(RuntimeError, match="must be awaited inside a run"):
        _run(_store(fake).get("notion-one", _USER))

    assert fake.calls == []


def test_agent_get_resolves_outside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """As a channel callback does."""
    monkeypatch.setattr(_connections, "resolve_runtime_configurable", lambda _runtime: None)
    fake = _FakeAgentAuth(
        _connection_page("docs", "secret", "agent"),
        credential_pages=[_workspace_credential_page("docs")],
    )

    assert _run(_store(fake).get("docs", _AGENT)) == "tok-1"


def test_deployment_secret_resolves_outside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_connections, "resolve_runtime_configurable", lambda _runtime: None)
    fake = _FakeAgentAuth(
        _connection_page("docs", "secret", "agent"),
        credential_pages=[_workspace_credential_page("docs")],
    )

    assert asyncio.run(_store(fake).resolve_deployment_secret("docs")) == "tok-1"


def test_unknown_owner_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown key 'scope'"):
        _run(connections.get("notion-one", {"type": "agent", "scope": "x"}))


def test_non_mapping_owner_is_rejected() -> None:
    untyped_get = cast("Any", connections.get)
    with pytest.raises(ValueError, match="connection owner is required"):
        _run(untyped_get("notion-one", "agent"))


def test_store_receives_the_resolved_request() -> None:
    seen: list[ConnectionRequest] = []

    class RecordingStore:
        async def fetch(self, request: ConnectionRequest) -> str | None:
            seen.append(request)
            return "tok-1"

    assert _run(_Connections(RecordingStore()).get(" notion-one ", _AGENT)) == "tok-1"
    assert seen == [
        {"slug": "notion-one", "owner": {"type": "agent"}, "identity": None},
    ]


def test_agent_auth_context_resolves_http_channel_provider_without_issuer() -> None:
    clear_principal_resolve_cache_for_tests()
    register_declared_source_providers(
        [
            SimpleNamespace(name="orders", kind="shopify"),
            SimpleNamespace(name="returns", kind="shopify"),
            SimpleNamespace(name="tickets", kind="zendesk"),
        ]
    )
    seen: list[dict[str, object]] = []

    def request(
        method: str, path: str, *, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        assert method == "POST"
        assert path == "/v1/agent-auth/principals"
        assert body is not None
        seen.append(body)
        return 200, {"principal": {"id": f"{body['provider']}:{body['subject']}"}}

    try:
        for provider in ["shopify", "shopify", "zendesk"]:
            identity = build_runtime_identity(
                {
                    "langgraph_auth_user": {
                        "identity": "user-123",
                        "mda_user_id": "user-123",
                        "mda_user_kind": "person",
                        "mda_source_provider": provider,
                    }
                }
            )
            context = resolve_agent_auth_context(identity, _ENV, request_fn=request)
            assert context.principal_id == f"{provider}:user-123"
            assert seen[-1] == {"provider": provider, "subject": "user-123"}
        assert len(seen) == 2
    finally:
        clear_principal_resolve_cache_for_tests()


def test_agent_auth_context_resolves_verified_studio_users_as_langsmith_people() -> None:
    clear_principal_resolve_cache_for_tests()
    seen: list[dict[str, object]] = []

    def request(
        method: str, path: str, *, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        assert method == "POST"
        assert path == "/v1/agent-auth/principals"
        assert body is not None
        seen.append(body)
        return 200, {"principal": {"id": f"{body['issuer']}:{body['subject']}"}}

    try:
        for user_id, workspace_id in [
            ("studio-alice", "workspace-1"),
            ("studio-bob", "workspace-1"),
            ("studio-alice", "workspace-2"),
        ]:
            identity = build_runtime_identity(
                {
                    "langgraph_auth_user": {
                        "kind": "StudioUser",
                        "identity": f"legacy-{user_id}",
                        "ls_user_id": user_id,
                        "is_authenticated": True,
                    }
                }
            )
            context = resolve_agent_auth_context(
                identity, {**_ENV, "LANGSMITH_WORKSPACE_ID": workspace_id}, request_fn=request
            )
            assert context.principal_id == f"{workspace_id}:{user_id}"
            assert seen[-1] == {
                "provider": "langsmith",
                "issuer": workspace_id,
                "subject": user_id,
            }
        assert len(seen) == 3
    finally:
        clear_principal_resolve_cache_for_tests()


@pytest.mark.parametrize("user_id", [None, "", "langgraph-studio-user"])
def test_agent_auth_context_refuses_studio_without_a_person_id(user_id: str | None) -> None:
    def request(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
        pytest.fail("an unverified Studio caller must not reach Agent Auth")

    identity = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "kind": "StudioUser",
                "identity": user_id,
                "is_authenticated": True,
            }
        }
    )
    with pytest.raises(CredentialStoreError, match="principal"):
        resolve_agent_auth_context(identity, _ENV, request_fn=request)


@pytest.mark.parametrize("provider", ["http", "slack"])
def test_agent_auth_context_binds_a_stamped_principal(provider: str) -> None:
    identity = cast(
        "Any",
        {
            "user": {"kind": "person", "id": "source-1"},
            "source": {"provider": provider},
            "agent_auth_principal_id": "aa-principal-1",
        },
    )

    context = resolve_agent_auth_context(identity, _ENV)

    assert context.base_url == "https://api.smith.langchain.com"
    assert context.agent_id == "deployment-1"
    assert context.principal_id == "aa-principal-1"
    assert context.api_key == "ls-key"


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"LANGCHAIN_API_KEY": "deployment-key"}, "deployment-key"),
    ],
)
def test_agent_auth_context_accepts_the_deployment_key(keys: dict[str, str], expected: str) -> None:
    identity = cast(
        "Any",
        {
            "user": {"kind": "person", "id": "source-1"},
            "source": {"provider": "http"},
            "agent_auth_principal_id": "aa-principal-1",
        },
    )
    context = resolve_agent_auth_context(
        identity,
        {
            "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
            "LANGSMITH_WORKSPACE_ID": "workspace-1",
            **keys,
        },
    )

    assert context.api_key == expected


def test_agent_auth_context_refuses_an_unauthenticated_run() -> None:
    with pytest.raises(CredentialStoreError, match="require an authenticated user principal"):
        resolve_agent_auth_context(None, _ENV)


def test_agent_auth_context_works_under_local_dev_without_deployment() -> None:
    identity = cast(
        "Any",
        {
            "user": {"kind": "person", "id": "source-1"},
            "source": {"provider": "http"},
            "agent_auth_principal_id": "aa-principal-1",
        },
    )

    context = resolve_agent_auth_context(
        identity,
        {
            "LANGSMITH_API_KEY": "ls-key",
            "LANGSMITH_WORKSPACE_ID": "workspace-1",
            "MDA_LOCAL_DEV": "1",
        },
    )

    assert context.principal_id == "aa-principal-1"
    assert context.agent_id is None
    assert context.workspace_id == "workspace-1"


def test_agent_auth_context_refuses_service_principals() -> None:
    identity = cast(
        "Any",
        {"user": {"kind": "service", "id": "mda:local-dev"}, "source": {"provider": "studio"}},
    )

    with pytest.raises(CredentialStoreError, match="person principal"):
        resolve_agent_auth_context(identity, _ENV)


def test_agent_auth_context_refuses_runs_a_channel_event_started() -> None:
    # A GitHub event starts a run that acts as the App, not as its sender.
    identity = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "identity": "mda:channel",
                "mda_user_id": "mda:channel",
                "mda_user_kind": "service",
                "mda_source_provider": "github",
                "mda_source_thread_id": "github:thread-1",
            }
        }
    )

    with pytest.raises(
        CredentialStoreError,
        match="a github event starts an agent run that acts as the channel's registered app",
    ):
        resolve_agent_auth_context(identity, _ENV)


def test_agent_auth_context_refuses_a_channel_conversation_even_for_a_studio_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    studio = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "kind": "StudioUser",
                "identity": "legacy-studio-alice",
                "ls_user_id": "studio-alice",
                "is_authenticated": True,
            }
        }
    )
    channel = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "identity": "mda:channel",
                "mda_user_id": "mda:channel",
                "mda_user_kind": "service",
                "mda_source_provider": "github",
            }
        }
    )
    monkeypatch.setattr(connections_mod, "on_channel_thread", lambda: True)

    with pytest.raises(CredentialStoreError, match=re.escape(CHANNEL_THREAD_USER_CONNECTION_ERROR)):
        resolve_agent_auth_context(studio, {**_ENV, "LANGSMITH_WORKSPACE_ID": "workspace-1"})
    # The channel's own run keeps its more specific reason.
    with pytest.raises(CredentialStoreError, match="acts as the channel's registered app"):
        resolve_agent_auth_context(channel, _ENV)
    # A linked Slack person continues the channel's own conversation.
    slack_person = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "identity": "principal-1",
                "mda_user_id": "U123",
                "mda_user_kind": "person",
                "mda_source_provider": "slack",
                "mda_source_thread_id": "slack:T1:C1:1.0",
                "mda_agent_auth_principal_id": "principal-1",
            }
        }
    )
    assert resolve_agent_auth_context(slack_person, _ENV).principal_id == "principal-1"


@pytest.mark.parametrize("provider", ["github", "linear"])
def test_agent_auth_context_refuses_a_sender_resolve_user_did_not_map(provider: str) -> None:
    # The sender's own principal, without the stamp a mapped user gets.
    sender = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "identity": "3f2a9c1e-7b4d-4e0a-9c55-1d2e8f6a0b71",
                "mda_user_id": "3f2a9c1e-7b4d-4e0a-9c55-1d2e8f6a0b71",
                "mda_source_provider": provider,
                "mda_source_thread_id": f"{provider}:3/1",
            }
        }
    )
    assert sender is not None
    assert sender["user"]["kind"] == "person"

    with pytest.raises(
        CredentialStoreError,
        match=f"user-owned connections are unavailable to {provider} senders that resolve_user",
    ):
        resolve_agent_auth_context(sender, _ENV)


def test_agent_auth_context_uses_staged_developer_principal_for_local_studio() -> None:
    identity = cast(
        "Any",
        {"user": {"kind": "service", "id": "mda:local-dev"}, "source": {"provider": "studio"}},
    )

    context = resolve_agent_auth_context(
        identity,
        {
            "LANGSMITH_API_KEY": "ls-key",
            "LANGSMITH_WORKSPACE_ID": "workspace-1",
            "MDA_LOCAL_DEV": "1",
            "MDA_AGENT_AUTH_PRINCIPAL_ID": "developer-principal-1",
        },
    )

    assert context.principal_id == "developer-principal-1"
    assert context.agent_id is None
    assert context.workspace_id == "workspace-1"


def test_agent_auth_context_resolves_validated_token_sub_not_user_field() -> None:
    identity = cast(
        "Any",
        {
            "user": {"kind": "person", "id": "alice@example.com"},
            "source": {"provider": "http"},
            "identity_provider": "supabase",
            "identity_issuer": "https://ref.supabase.co/auth/v1",
            "identity_subject": "u1",
            "claims": {"iss": "https://ref.supabase.co/auth/v1", "sub": "u1"},
        },
    )
    seen: list[dict[str, object]] = []

    def request(
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        del method, path
        if body is not None:
            seen.append(body)
        return 200, {"principal": {"id": "aa-principal-1"}}

    context = resolve_agent_auth_context(identity, _ENV, request_fn=request)

    assert context.principal_id == "aa-principal-1"
    assert seen == [
        {
            "provider": "supabase",
            "issuer": "https://ref.supabase.co/auth/v1",
            "subject": "u1",
        }
    ]


def test_github_token_resolves_to_the_deployments_app_connection() -> None:
    from managed_deepagents._channels.github_connections import (  # noqa: PLC0415
        GITHUB_TOKEN_SLUG,
    )
    from managed_deepagents._channels.reserved_connections import (  # noqa: PLC0415
        reserved_agent_connection_slug,
    )

    assert reserved_agent_connection_slug(GITHUB_TOKEN_SLUG, "dep-1") == "mda/github-app/dep-1"
    assert reserved_agent_connection_slug(GITHUB_TOKEN_SLUG, None) == GITHUB_TOKEN_SLUG
    assert reserved_agent_connection_slug("github", "dep-1") == "github"


def test_github_app_connection_slug_names_the_app_and_its_values() -> None:
    from managed_deepagents._channels.github_connections import (  # noqa: PLC0415
        github_app_connection_slug,
    )

    assert github_app_connection_slug("dep-1") == "mda/github-app/dep-1"
    assert github_app_connection_slug("dep-1", "webhook-secret") == (
        "mda/github-app/dep-1/webhook-secret"
    )


def test_github_token_reads_the_app_connection_for_agent_owners() -> None:
    fake = _FakeAgentAuth({"items": [], "next_cursor": None})

    with pytest.raises(ConnectionNotFoundError):
        _run(_store(fake).get("mda/github-token", _AGENT))

    assert "/v1/agent-auth/connections?slug=mda%2Fgithub-app%2Fdeployment-1" in fake.calls[0][0]


def test_github_token_is_not_supported_as_a_user_connection_yet() -> None:
    fake = _FakeAgentAuth({"items": [], "next_cursor": None})

    with pytest.raises(ValueError, match="not supported as a user connection yet") as raised:
        _store(fake).get("mda/github-token", _USER)
    assert 'connections.get(\'mda/github-token\', {"type": "agent"})' in str(raised.value)
    with pytest.raises(ValueError, match="not supported as a user connection yet"):
        _store(fake).get("mda/github-token", {"type": "user", "id": "user-1"})
    # The Linear app's token is agent-only too, with its own wording.
    with pytest.raises(ValueError, match="the Linear app's token") as linear:
        _store(fake).get("mda/linear-token", _USER)
    assert "to act in Linear as the app" in str(linear.value)
    # So is the Slack app's bot token.
    with pytest.raises(ValueError, match="the Slack app's bot token") as slack:
        _store(fake).get("mda/slack-bot-token", _USER)
    assert "to act in Slack as the app" in str(slack.value)
    assert fake.calls == []
    # Other slugs and the agent connection are unaffected.
    _store(fake).get("notion-one", _USER)
    _store(fake).get("mda/github-token", _AGENT)
