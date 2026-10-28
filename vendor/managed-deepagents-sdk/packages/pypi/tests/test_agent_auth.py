"""Tests for Agent Auth credentials + oauth-authorization-sessions client."""

from __future__ import annotations

import httpx
import pytest

import managed_deepagents._agent_auth as aa
from managed_deepagents._agent_auth_http import (
    AGENT_AUTH_PROBLEM_HINT_MAX_CHARS,
    agent_auth_headers,
    agent_auth_problem_hint,
    missing_workspace_connection_slug,
)


@pytest.fixture(autouse=True)
def _clear_principal_cache() -> None:
    aa.clear_principal_resolve_cache_for_tests()
    yield
    aa.clear_principal_resolve_cache_for_tests()


@pytest.fixture
def ctx() -> aa.AgentAuthContext:
    return aa.AgentAuthContext(
        base_url="https://api.smith.langchain.com",
        agent_id="00000000-0000-4000-8000-000000000002",
        principal_id="00000000-0000-4000-8000-000000000001",
        api_key="test-api-key",
        workspace_id="test-workspace",
    )


def _connection_list(
    slug: str = "notion", kind: str = "oauth2", connection_id: str = "conn-1"
) -> dict:
    return {
        "items": [
            {
                "id": connection_id,
                "slug": slug,
                "credential": {"kind": kind},
            }
        ],
        "next_cursor": None,
    }


def _connection_for_owner(
    *,
    slug: str = "notion",
    kind: str = "oauth2",
    connection_id: str = "conn-1",
    credential_id: str | None = "cred-1",
) -> dict:
    credential: dict[str, object] = {"kind": kind}
    if credential_id is not None:
        credential["credential_id"] = credential_id
    return {
        "id": connection_id,
        "slug": slug,
        "credential": credential,
    }


def test_agent_secret_does_not_fall_back_to_workspace_owner(
    ctx: aa.AgentAuthContext,
) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        paths.append(path)
        if path.startswith("/v1/agent-auth/connections?") and method == "GET":
            return 200, _connection_list("tavily", "secret", "conn-tavily")
        if "credential_owner_type=agent" in path and "/connections/" in path:
            return 200, _connection_for_owner(
                slug="tavily",
                kind="secret",
                connection_id="conn-tavily",
                credential_id=None,
            )
        if path.startswith("/v1/agent-auth/credentials?"):
            return 200, {"items": []}
        raise AssertionError(f"unexpected {method} {path}")

    auth = aa.AgentAuth(ctx, request=_request)

    assert auth.find_workspace_credential_id("tavily") is None
    assert f"credential_owner_id={ctx.agent_id}" in paths[1]
    assert "owner_type=agent" in paths[2]
    assert len(paths) == 3
    assert all("credential_owner_type=workspace" not in path for path in paths)


def test_read_returns_access_token(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        if path.startswith("/v1/agent-auth/connections?") and method == "GET":
            return 200, _connection_list()
        if (
            path.startswith("/v1/agent-auth/connections/conn-1?")
            and "credential_owner_type=user" in path
            and f"credential_owner_id={ctx.principal_id}" in path
        ):
            return 200, _connection_for_owner()
        if path.endswith("/oauth2"):
            return 200, {"oauth2": {"access_token": "ya29.local"}}
        raise AssertionError(f"unexpected {method} {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.read_user_access_token("notion") == "ya29.local"


def test_user_grant_uses_connection_for_owner(ctx: aa.AgentAuthContext) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        paths.append(path)
        if path.startswith("/v1/agent-auth/connections?") and method == "GET":
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 200, _connection_for_owner(credential_id="cred-notion")
        if path == "/v1/agent-auth/credentials/cred-notion/oauth2":
            return 200, {"oauth2": {"access_token": "ya29.filtered"}}
        raise AssertionError(f"unexpected {method} {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.read_user_access_token("notion") == "ya29.filtered"
    assert paths[0].startswith("/v1/agent-auth/connections?")
    assert "credential_owner_type=user" in paths[1]
    assert f"credential_owner_id={ctx.principal_id}" in paths[1]
    assert "/credentials?" not in paths[1]


def test_read_never_starts_a_session(ctx: aa.AgentAuthContext) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 200, _connection_for_owner(credential_id=None)
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.read_user_access_token("notion") is None
    assert all(not path.startswith("/v1/agent-auth/oauth-authorization-sessions") for path in paths)
    assert all("/credentials?" not in path for path in paths)


def test_lapsed_grant_reads_as_unauthorized(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 200, _connection_for_owner()
        if path.endswith("/oauth2"):
            return 409, {"detail": "OAuth authorization is required"}
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.read_user_access_token("notion") is None


def test_forbidden_user_owner_lookup_surfaces_forbidden(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 403, {"detail": "access denied"}
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    with pytest.raises(
        aa.CredentialStoreError, match=r"get connection for owner failed \(forbidden\)"
    ):
        auth.read_user_access_token("notion")


def test_provider_lookup_walks_pages(ctx: aa.AgentAuthContext) -> None:
    """oauth-providers is paginated and has no slug filter."""
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        if "cursor=prov-1" in path:
            return 200, {"items": [{"id": "prov-2", "slug": "notion"}], "next_cursor": None}
        return 200, {"items": [{"id": "prov-1", "slug": "linear"}], "next_cursor": "prov-1"}

    assert aa.AgentAuth(ctx, request=_request).resolve_oauth_provider_id("notion") == "prov-2"
    assert len(paths) == 2


def test_connection_lookup_filters_by_slug(ctx: aa.AgentAuthContext) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        return 200, _connection_list("team/notion", "oauth2")

    assert aa.AgentAuth(ctx, request=_request).find_workspace_connection(
        "team/notion"
    ) == aa.WorkspaceConnection(connection_id="conn-1", kind="oauth2", owner_policy="")
    assert paths == ["/v1/agent-auth/connections?slug=team%2Fnotion"]


def test_connection_lookup_reports_no_match_from_the_slug_filter(
    ctx: aa.AgentAuthContext,
) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        return 200, {"items": [], "next_cursor": None}

    assert aa.AgentAuth(ctx, request=_request).find_workspace_connection("notion") is None
    assert len(paths) == 1


def test_connection_lookup_pages_when_the_slug_filter_is_ignored(
    ctx: aa.AgentAuthContext,
) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        if "cursor=conn-1" in path:
            return 200, _connection_list("notion", "secret", "conn-2")
        return 200, {**_connection_list("linear", "oauth2"), "next_cursor": "conn-1"}

    assert aa.AgentAuth(ctx, request=_request).find_workspace_connection(
        "notion"
    ) == aa.WorkspaceConnection(connection_id="conn-2", kind="secret", owner_policy="")
    assert len(paths) == 3
    assert "slug=" not in paths[1]
    assert "cursor=conn-1" in paths[2]


def test_provider_lookup_reports_an_unregistered_slug(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method, path
        return 200, {"items": [{"id": "prov-1", "slug": "linear"}], "next_cursor": None}

    with pytest.raises(aa.AgentAuthUnknownProviderError):
        aa.AgentAuth(ctx, request=_request).resolve_oauth_provider_id("notion")


def test_resolve_principal(ctx: aa.AgentAuthContext) -> None:
    def _request(
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert method == "POST"
        assert path == "/v1/agent-auth/principals"
        assert body == {
            "provider": "supabase",
            "issuer": "https://ref.supabase.co/auth/v1",
            "subject": "alice@example.com",
        }
        return 201, {"principal": {"id": "00000000-0000-4000-8000-000000000099"}}

    auth = aa.AgentAuth(ctx, request=_request)
    assert (
        auth.resolve_principal(
            {
                "provider": "supabase",
                "issuer": "https://ref.supabase.co/auth/v1",
                "subject": "alice@example.com",
            }
        )
        == "00000000-0000-4000-8000-000000000099"
    )


def test_resolve_principal_optional_issuer_and_cache(ctx: aa.AgentAuthContext) -> None:
    seen: list[dict[str, object]] = []

    def request(
        method: str, path: str, *, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        assert body is not None
        seen.append(body)
        return 201, {"principal": {"id": body.get("issuer", "no-issuer")}}

    auth = aa.AgentAuth(ctx, request=request)
    identity: aa.ResolvePrincipalIdentity = {"provider": "shopify", "subject": "user-123"}
    assert auth.resolve_principal({**identity, "issuer": None}) == "no-issuer"
    assert auth.resolve_principal(identity) == "no-issuer"
    assert auth.resolve_principal({**identity, "issuer": "None"}) == "None"
    assert seen == [identity, {**identity, "issuer": "None"}]


@pytest.mark.parametrize("issuer", ["", "   "])
def test_resolve_principal_rejects_blank_issuer(ctx: aa.AgentAuthContext, issuer: str) -> None:
    def request(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
        pytest.fail("blank issuer must be rejected before the request")

    with pytest.raises(aa.CredentialStoreError, match="issuer must be nonempty"):
        aa.AgentAuth(ctx, request=request).resolve_principal(
            {"provider": "shopify", "subject": "user-123", "issuer": issuer}
        )


def test_resolve_principal_cache_is_scoped_to_workspace(ctx: aa.AgentAuthContext) -> None:
    identity = {
        "provider": "supabase",
        "issuer": "https://ref.supabase.co/auth/v1",
        "subject": "alice@example.com",
    }
    calls: list[str] = []

    def _request(
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        del method, body
        assert path == "/v1/agent-auth/principals"
        calls.append("post")
        if len(calls) == 1:
            return 201, {"principal": {"id": "00000000-0000-4000-8000-000000000011"}}
        return 201, {"principal": {"id": "00000000-0000-4000-8000-000000000022"}}

    first = aa.AgentAuth(ctx, request=_request)
    second = aa.AgentAuth(
        aa.AgentAuthContext(
            base_url=ctx.base_url,
            principal_id=ctx.principal_id,
            agent_id=ctx.agent_id,
            api_key=ctx.api_key,
            workspace_id="other-workspace",
        ),
        request=_request,
    )
    assert first.resolve_principal(identity) == "00000000-0000-4000-8000-000000000011"
    assert second.resolve_principal(identity) == "00000000-0000-4000-8000-000000000022"
    assert first.resolve_principal(identity) == "00000000-0000-4000-8000-000000000011"
    assert len(calls) == 2


def test_start_authorization_session(ctx: aa.AgentAuthContext) -> None:
    def _request(
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if path.startswith("/v1/agent-auth/connections"):
            return 200, {
                "items": [
                    {
                        "connection_id": "conn-1",
                        "slug": "notion",
                        "kind": "oauth2",
                        "owner_policy": "user",
                    }
                ],
                "next_cursor": None,
            }
        if path == "/v1/agent-auth/oauth-authorization-sessions":
            assert body == {
                "connection_id": "conn-1",
                "owner_type": "user",
                "owner_id": ctx.principal_id,
            }
            return 201, {
                "id": "sess-1",
                "verification_url": "https://idp.example/connect",
                "status": "pending",
            }
        raise AssertionError(f"unexpected {method} {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.start_authorization_session("notion") == {
        "slug": "notion",
        "auth_id": "sess-1",
        "connect_url": "https://idp.example/connect",
    }


def test_missing_workspace_connection(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        if path.startswith("/v1/agent-auth/connections"):
            return 200, {"items": [], "next_cursor": None}
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    with pytest.raises(
        aa.CredentialStoreError, match="workspace connection 'notion' is not configured"
    ) as raised:
        auth.start_authorization_session("notion")

    # A channel only sees this message as a string, so it must parse back.
    assert missing_workspace_connection_slug(str(raised.value)) == "notion"


def test_missing_connection_slug_reads_the_npm_quoting() -> None:
    assert (
        missing_workspace_connection_slug('workspace connection "my-notion" is not configured')
        == "my-notion"
    )


def test_missing_connection_slug_ignores_an_unrelated_failure() -> None:
    assert missing_workspace_connection_slug("connect failed for /secret/path") is None


def test_credential_ids_are_escaped_in_the_path(ctx: aa.AgentAuthContext) -> None:
    paths: list[str] = []

    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        paths.append(path)
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 200, _connection_for_owner(credential_id="a/b")
        if path.endswith("/oauth2"):
            return 200, {"oauth2": {"access_token": "ya29.local"}}
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    assert auth.read_user_access_token("notion") == "ya29.local"
    assert paths[2] == "/v1/agent-auth/credentials/a%2Fb/oauth2"


def test_agent_auth_headers_use_only_bound_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "env-key")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "env-workspace")
    assert agent_auth_headers() == {
        "accept": "application/json",
        "content-type": "application/json",
        "x-langsmith-product": "mda",
    }
    assert agent_auth_headers(api_key="bound-key", workspace_id="bound-workspace") == {
        "accept": "application/json",
        "content-type": "application/json",
        "x-langsmith-product": "mda",
        "x-api-key": "bound-key",
        "x-tenant-id": "bound-workspace",
    }


def test_request_json_bounds_timeout(
    monkeypatch: pytest.MonkeyPatch, ctx: aa.AgentAuthContext
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr("managed_deepagents._agent_auth_http._CLIENT", client)
        assert aa.AgentAuth(ctx)._urlopen_json("GET", "/v1/agent-auth/oauth-providers") == (
            200,
            {},
        )
    request = requests[0]
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), aa.AGENT_AUTH_TIMEOUT_SECONDS
    )
    assert request.headers["x-api-key"] == "test-api-key"
    assert request.headers["x-tenant-id"] == "test-workspace"


def test_authorization_conflict_requires_a_specific_reauth_signal() -> None:
    assert aa._is_authorization_required(409, {"detail": "OAuth authorization is required"})
    assert not aa._is_authorization_required(409, {"detail": "Authorization policy conflict"})


def test_problem_hint_reads_only_detail_and_remedy() -> None:
    assert agent_auth_problem_hint(
        {
            "title": "Service Unavailable",
            "detail": "OAuth callback is not configured",
            "remedy": "Configure AGENT_AUTH_OAUTH_CALLBACK_URL and retry",
            "secret": "must-not-leak",
            "oauth2": {"access_token": "ya29.must-not-leak"},
        }
    ) == ("OAuth callback is not configured Configure AGENT_AUTH_OAUTH_CALLBACK_URL and retry")


def test_problem_hint_is_absent_without_a_usable_string() -> None:
    assert agent_auth_problem_hint(None) is None
    assert agent_auth_problem_hint({}) is None
    assert agent_auth_problem_hint({"detail": "   "}) is None
    assert agent_auth_problem_hint({"detail": 503, "remedy": None}) is None


def test_problem_hint_collapses_repeated_multiline_and_overlong_bodies() -> None:
    assert (
        agent_auth_problem_hint({"detail": "Service Unavailable", "remedy": "Service Unavailable"})
        == "Service Unavailable"
    )
    assert agent_auth_problem_hint({"detail": "line one\n\tline two"}) == "line one line two"
    assert agent_auth_problem_hint({"detail": "z" * 400}) == (
        "z" * AGENT_AUTH_PROBLEM_HINT_MAX_CHARS + "..."
    )


def test_problem_hint_truncates_on_unicode_code_points() -> None:
    at_limit = "z" * (AGENT_AUTH_PROBLEM_HINT_MAX_CHARS - 1) + "🙂"
    assert agent_auth_problem_hint({"detail": at_limit}) == at_limit
    assert agent_auth_problem_hint({"detail": at_limit + "tail"}) == at_limit + "..."


def test_session_create_failure_quotes_the_problem_detail(
    ctx: aa.AgentAuthContext,
) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path == "/v1/agent-auth/oauth-authorization-sessions":
            return 400, {
                "title": "Bad Request",
                "detail": "connection_id, principal_id, and agent_id are required",
                "remedy": "Correct the request and retry",
            }
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    with pytest.raises(
        RuntimeError,
        match=(
            r"Agent Auth create oauth-authorization-session failed \(status 400\): "
            r"connection_id, principal_id, and agent_id are required "
            r"Correct the request and retry"
        ),
    ):
        auth.start_authorization_session("notion")


def test_failed_oauth2_errors_omit_the_response_body(ctx: aa.AgentAuthContext) -> None:
    def _request(method: str, path: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        del method
        if path.startswith("/v1/agent-auth/connections?"):
            return 200, _connection_list()
        if path.startswith("/v1/agent-auth/connections/conn-1?"):
            return 200, _connection_for_owner()
        if path.endswith("/oauth2"):
            return 500, {"oauth2": {"access_token": "ya29.must-not-leak"}}
        raise AssertionError(f"unexpected {path}")

    auth = aa.AgentAuth(ctx, request=_request)
    with pytest.raises(
        RuntimeError, match=r"Agent Auth get oauth2 failed \(status 500\)"
    ) as raised:
        auth.read_user_access_token("notion")
    assert "ya29.must-not-leak" not in str(raised.value)
