"""Independent LangSmith login, membership revocation, and desktop handoff."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.runnables import RunnableConfig
from runtime_api.loop import GatewayCredential
from sqlalchemy import event, update

from openswe.agent_api.connections import SENDER_USER_KEY
from openswe.dashboard import auth_routes, authentication, langsmith_login, langsmith_oauth, oauth
from openswe.dashboard.langsmith_login import LangSmithAccount
from openswe.dashboard.oauth_credentials import load_credential, save_credential
from openswe.dashboard.principal import DashboardPrincipal
from openswe.dashboard.profiles import PROFILES, get_profile
from openswe.dashboard.profiles import router as profiles_router
from openswe.dashboard.user_preferences import USER_PREFERENCES
from openswe.database import postgres
from openswe.encryption import decrypt_token, encrypt_token
from openswe.github import dashboard_routes
from openswe.review.routes import router as review_router
from openswe.threads.pins import THREAD_PINS
from openswe.threads.principals import Principal
from openswe.users import AccountConflict, UnauthorizedUser, User
from openswe.users.models import UserIdentity

ISSUER = "https://api.smith.langchain.com"
LANGSMITH_TOKEN = jwt.encode({"iss": ISSUER}, Ed25519PrivateKey.generate(), algorithm="EdDSA")
LANGSMITH_BEARERS = [
    LANGSMITH_TOKEN,
    "opaque-login-token",
    *[
        jwt.encode(
            {
                "aud": "authenticated",
                "sub": "11111111-1111-4111-8111-111111111111",
                "email": "login@example.com",
                "exp": 4102444800,
                **claims,
            },
            "test-login-signing-key-at-least-32-bytes",
            algorithm="HS256",
        )
        for claims in [{"iss": "https://supabase.example/auth/v1"}, {"aud": ["authenticated"]}]
    ],
]
ALLOWED_ORG = "f5c798a2-2155-4999-ad27-6d466bd26e1c"
CALLBACK = "https://dashboard.example/dashboard/api/langsmith/callback"


@dataclass
class LangSmithServer:
    subject: str = "5bb3fb19-f328-4815-8fe8-0483b41312c2"
    organization: str = ALLOWED_ORG
    deployment_org: str = ALLOWED_ORG
    deployment_workspace: str = "deployment-workspace"
    discoveries: int = 0
    discovery_unavailable: bool = False
    clock: float = 0.0
    member: bool = True
    revoked: bool = False
    unavailable: bool = False
    rate_limited: int = 0
    refreshes: int = 0
    authorizations: int = 0
    request_identity: dict[str, object] = field(default_factory=dict)
    request_status: int = 200
    request_headers: list[httpx2.Headers] = field(default_factory=list)
    revocations: list[str] = field(default_factory=list)
    checking_membership: asyncio.Event | None = None
    resume_membership: asyncio.Event | None = None

    async def expire_authorization(self, key: str) -> None:
        credential = await load_credential("langsmith_login", key)
        assert credential is not None
        assert await credential.authorize_login(
            ALLOWED_ORG, datetime.now(UTC) - timedelta(seconds=1)
        )

    async def respond(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/auth":
            self.request_headers.append(request.headers)
            return httpx2.Response(
                self.request_status,
                json={
                    "organization_id": self.organization,
                    "tenant_id": self.deployment_workspace,
                    "ls_user_id": self.subject,
                    "user_email": "same@example.com",
                    **self.request_identity,
                },
            )
        if request.url.path == "/auth/public":
            self.discoveries += 1
            if self.discovery_unavailable:
                return httpx2.Response(503, json={"detail": "unavailable"})
            assert request.headers["x-api-key"] == "deployment-key"
            assert "authorization" not in request.headers
            return httpx2.Response(
                200,
                json={
                    "organization_id": self.deployment_org,
                    "tenant_id": self.deployment_workspace,
                },
            )
        if request.url.path == "/.well-known/openid-configuration":
            return httpx2.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/oauth/authorize",
                    "token_endpoint": ISSUER + "/oauth/token",
                    "userinfo_endpoint": ISSUER + "/userinfo",
                    "revocation_endpoint": ISSUER + "/oauth/revoke",
                },
            )
        if request.url.path == "/oauth/revoke":
            if self.unavailable:
                return httpx2.Response(503, json={"detail": "unavailable"})
            self.revocations.append(parse_qs(request.content.decode())["token"][0])
            return httpx2.Response(200)
        if request.url.path == "/oauth/token":
            if self.rate_limited:
                self.rate_limited -= 1
                return httpx2.Response(
                    429, text="<html>Too Many Requests</html>", headers={"Retry-After": "0"}
                )
            body = parse_qs(request.content.decode())
            assert body["resource"] == [ISSUER]
            assert body["client_secret"] == ["test-client-secret"]
            if body["grant_type"] == ["refresh_token"]:
                self.refreshes += 1
                if self.revoked:
                    return httpx2.Response(400, json={"error": "invalid_grant"})
            else:
                assert body["code_verifier"][0]
                assert body["redirect_uri"] == [CALLBACK]
            return httpx2.Response(
                200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
            )
        assert request.headers["authorization"] == "Bearer access"
        if request.url.path == "/userinfo":
            self.authorizations += 1
            return httpx2.Response(
                200,
                json={
                    "sub": self.subject,
                    "ls_org_id": self.organization,
                    "email": "same@example.com",
                    "name": "LangSmith Person",
                },
            )
        assert request.url.path == "/orgs"
        assert request.url.params["skip_create"] == "true"
        if self.checking_membership is not None and self.resume_membership is not None:
            self.checking_membership.set()
            await self.resume_membership.wait()
        if self.unavailable:
            return httpx2.Response(503, json={"detail": "unavailable"})
        return httpx2.Response(200, json=[{"id": self.organization}] if self.member else [])

    async def desktop_login(self, client: httpx.AsyncClient) -> httpx.Response:
        start = await client.get(
            "/dashboard/api/auth/langsmith/login",
            params={
                "desktop": "true",
                "desktop_handoff": oauth.pkce_s256_challenge("verifier"),
                "desktop_port": 43210,
            },
        )
        assert start.status_code == 302
        query = parse_qs(urlparse(start.headers["location"]).query)
        assert query["scope"] == ["openid profile email offline_access"]
        assert query["resource"] == [ISSUER]
        assert query["organization_id"] == [self.deployment_org]
        assert query["workspace_id"] == [self.deployment_workspace]
        assert query["redirect_uri"] == [CALLBACK]
        callback = await client.get(
            "/dashboard/api/langsmith/callback",
            params={"state": query["state"][0], "code": "code"},
        )
        return callback

    async def exchange(
        self, client: httpx.AsyncClient, callback: httpx.Response, verifier: str = "verifier"
    ) -> httpx.Response:
        code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
        return await client.post(
            "/dashboard/api/auth/desktop/exchange", json={"code": code, "verifier": verifier}
        )


@pytest.fixture
async def langsmith_client(registry_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("LANGSMITH_CONNECTION_URL", ISSUER)
    monkeypatch.setenv("LANGSMITH_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("LANGSMITH_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LANGSMITH_API_KEY", "deployment-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", ISSUER)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    server = LangSmithServer()
    monkeypatch.setattr(langsmith_login, "monotonic", lambda: server.clock)
    monkeypatch.setattr(
        langsmith_login, "LANGSMITH_ORG_DISCOVERY", langsmith_login.LangSmithOrgDiscovery()
    )
    monkeypatch.setattr(
        langsmith_login, "pooled_transport", lambda: httpx2.MockTransport(server.respond)
    )
    monkeypatch.setattr(
        authentication, "LANGSMITH_ORG_DISCOVERY", langsmith_login.LANGSMITH_ORG_DISCOVERY
    )
    monkeypatch.setattr(
        authentication, "pooled_transport", lambda: httpx2.MockTransport(server.respond)
    )
    app = FastAPI()
    app.add_exception_handler(
        AccountConflict,
        lambda _request, exc: JSONResponse({"detail": str(exc)}, status_code=409),
    )
    app.include_router(auth_routes.router, prefix="/dashboard/api")
    app.include_router(profiles_router, prefix="/dashboard/api")
    app.include_router(dashboard_routes.router, prefix="/dashboard/api")
    app.include_router(review_router, prefix="/dashboard/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://dashboard.example"
    ) as client:
        yield server, client


@pytest.mark.parametrize(
    "headers",
    [
        *[{"Authorization": f"Bearer {token}"} for token in LANGSMITH_BEARERS],
        {"Authorization": "Bearer lsv2_pt_personal"},
        {"X-Api-Key": "lsv2_pt_personal"},
        {"X-Api-Key": "legacy-personal-key"},
        {"Cookie": "_ls_oauth_user=langsmith-session; analytics_id=abc; osw_oauth_state=private"},
    ],
)
@pytest.mark.parametrize("existing", [False, True])
async def test_langsmith_credentials_authenticate_without_a_robin_cookie(
    langsmith_client, headers: dict[str, str], existing: bool
) -> None:
    from openswe.api import agent_server_auth
    from openswe.threads.principals import require_principal

    server, client = langsmith_client
    other = await User.sign_in(
        "langsmith",
        server.subject if existing else "same-email-other-person",
        email="same@example.com",
        authorization=LangSmithAccount(
            sub=server.subject if existing else "same-email-other-person",
            ls_org_id=ALLOWED_ORG,
        ),
    )
    response = await client.get("/dashboard/api/me", headers=headers)
    assert response.status_code == 200
    user = await User.for_identity("langsmith", server.subject)
    assert user is not None
    assert (user.id == other.id) is existing
    assert response.json()["user_id"] == str(user.id)
    assert not response.json()["is_admin"]
    assert "set-cookie" not in response.headers
    assert oauth.COOKIE_NAME not in client.cookies
    assert await load_credential("langsmith_login", user.session_login) is None
    profile = await client.get("/dashboard/api/profile", headers=headers)
    assert profile.status_code == 200
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/dashboard/api/threads",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )
    assert (await oauth.require_session(request))["user_id"] == str(user.id)
    if (
        headers.get("Authorization") in {f"Bearer {token}" for token in LANGSMITH_BEARERS}
        or "Cookie" in headers
    ):
        assert (await require_principal(request)).person == user.session_login
    assert await agent_server_auth.authenticate(request.scope) == {
        "identity": user.session_login,
        "permissions": [],
    }
    forwarded = server.request_headers[0]
    assert forwarded["x-tenant-id"] == server.deployment_workspace
    for key, value in headers.items():
        assert forwarded[key] == ("_ls_oauth_user=langsmith-session" if key == "Cookie" else value)


@pytest.mark.parametrize("cookie", ["analytics_id=abc", '_ls_oauth_user=" "'])
async def test_langsmith_passthrough_requires_a_session_cookie(
    langsmith_client, cookie: str
) -> None:
    server, client = langsmith_client
    response = await client.get("/dashboard/api/me", headers={"Cookie": cookie})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert not server.request_headers


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer ghs_github-token",
        "Bearer github_pat_github-token",
        "Bearer agent-key",
        "Bearer machine-token",
        "Bearer lsv2_tr_tracing-key",
        "Bearer eyJ.malformed.jwt",
        "Basic other-credential",
        "Bearer "
        + jwt.encode(
            {"sub": "alice"}, "test-session-signing-key-at-least-32-bytes", algorithm="HS256"
        ),
        "Bearer "
        + jwt.encode(
            {"iss": "https://other-issuer.example", "aud": "authenticated"},
            Ed25519PrivateKey.generate(),
            algorithm="EdDSA",
        ),
    ],
    ids=[
        "github-app",
        "github-pat",
        "agent-key",
        "machine-token",
        "tracing-key",
        "malformed-jwt",
        "basic-auth",
        "robin-session",
        "foreign-issuer",
    ],
)
async def test_langsmith_passthrough_delegates_credential_rejection_to_langsmith(
    langsmith_client, authorization: str
) -> None:
    server, client = langsmith_client
    server.request_status = 401
    response = await client.get(
        "/dashboard/api/me",
        headers={
            "Authorization": authorization,
            "Cookie": "_ls_oauth_user=ambient-session",
        },
    )
    assert response.status_code == 401
    (forwarded,) = server.request_headers
    assert forwarded["authorization"] == authorization
    assert "cookie" not in forwarded
    assert server.discoveries == 1
    assert await User.for_identity("langsmith", server.subject) is None


@pytest.mark.parametrize(
    "identity,status",
    [
        ({"organization_id": "other-org"}, 403),
        ({"tenant_id": "other-workspace"}, 403),
        ({"ls_user_id": ""}, 403),
        ({"service_identity": "service"}, 403),
        ({"is_code_evaluator_jwt": True}, 403),
        ({"tenant_id": None}, 502),
    ],
)
@pytest.mark.parametrize(
    "token", LANGSMITH_BEARERS, ids=["oauth", "opaque", "supabase", "basic-login"]
)
async def test_langsmith_passthrough_refuses_untrusted_identities(
    langsmith_client, identity: dict[str, object], status: int, token: str
) -> None:
    server, client = langsmith_client
    server.request_identity = identity
    response = await client.get("/dashboard/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == status
    assert "set-cookie" not in response.headers
    assert await User.for_identity("langsmith", server.subject) is None


@pytest.mark.parametrize("status", [401, 403, 429, 503])
@pytest.mark.parametrize(
    "token", LANGSMITH_BEARERS, ids=["oauth", "opaque", "supabase", "basic-login"]
)
async def test_langsmith_passthrough_does_not_fall_back_to_ambient_cookies(
    langsmith_client, status: int, token: str
) -> None:
    server, client = langsmith_client
    await User.sign_in(
        "langsmith",
        server.subject,
        email="same@example.com",
        authorization=LangSmithAccount(sub=server.subject, ls_org_id=ALLOWED_ORG),
    )
    assert (
        await client.get("/dashboard/api/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    server.request_status = status
    response = await client.get(
        "/dashboard/api/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Cookie": "_ls_oauth_user=ambient-session",
            "X-User-Id": server.subject,
            "X-Organization-Id": "other-org",
        },
    )
    assert response.status_code == (status if status in {401, 403} else 503)
    assert len(server.request_headers) == 2
    forwarded = server.request_headers[-1]
    assert "cookie" not in forwarded
    assert "x-user-id" not in forwarded
    assert "x-organization-id" not in forwarded


async def test_langsmith_passthrough_keeps_robin_path_and_csrf_checks(langsmith_client) -> None:
    server, client = langsmith_client
    await User.sign_in(
        "langsmith",
        server.subject,
        email="same@example.com",
        authorization=LangSmithAccount(sub=server.subject, ls_org_id=ALLOWED_ORG),
    )
    headers = {"Cookie": "_ls_oauth_user=langsmith-session"}
    response = await client.get("/dashboard/api/me", headers=headers)
    assert response.status_code == 200
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/dashboard/api/threads",
            "headers": [(b"cookie", headers["Cookie"].encode()), (b"host", b"dashboard.example")],
        }
    )
    with pytest.raises(HTTPException, match="CSRF check failed"):
        oauth.require_same_origin_for_mutations(request)
    request = Request({**request.scope, "path": "/dashboard/api/review"})
    with pytest.raises(HTTPException, match="linked GitHub account"):
        await oauth.require_session(request)
    response = await client.get(
        "/dashboard/api/me",
        headers={"Authorization": f"Bearer {LANGSMITH_TOKEN}", "X-Tenant-Id": "other-workspace"},
    )
    assert response.status_code == 403


async def test_desktop_login_is_independent_and_pkce_bound(langsmith_client) -> None:
    server, client = langsmith_client
    github_user = await User.sign_in("github", "1", login="alice", email="alice@example.com")
    callback = await server.desktop_login(client)
    assert callback.status_code == 302
    assert urlparse(callback.headers["location"]).netloc == "127.0.0.1:43210"
    assert not any(
        header.startswith(oauth.COOKIE_NAME + "=")
        for header in callback.headers.get_list("set-cookie")
    )
    handoff = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
    client.cookies.set(oauth.COOKIE_NAME, handoff)
    assert (await client.get("/dashboard/api/me")).status_code == 401
    client.cookies.delete(oauth.COOKIE_NAME)
    assert (await server.exchange(client, callback, "wrong-verifier")).status_code == 400
    exchanged = await server.exchange(client, callback)
    assert exchanged.status_code == 200
    session = oauth.decode_session(exchanged.json()["session"])
    user = await User.for_identity("langsmith", server.subject)
    assert user is not None and user.id != github_user.id and user.github_login == ""
    assert session["sub"] == f"user:{user.id}" and session["provider"] == "langsmith"
    assert all(
        identity.provider == "langsmith" and identity.login == "" for identity in user.identities
    )
    assert await User.for_email("same@example.com") is None
    assert (await User.canonical_person({"id": session["sub"]}))["id"] == session["sub"]
    await PROFILES.put(session["sub"], {"draft_prs": False})
    await USER_PREFERENCES.put(session["sub"], {"follow_up_behavior": "queue"})
    profile = await get_profile(session["sub"])
    assert profile is not None and profile["draft_prs"] is False
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    me = await client.get("/dashboard/api/me")
    assert me.status_code == 200
    assert me.json()["github_login"] is None and me.json()["auth_provider"] == "langsmith"
    assert me.json()["requires_github_link"] is False
    assert me.json()["follow_up_behavior"] == "queue" and not me.json()["is_admin"]
    assert any(
        header.startswith(oauth.COOKIE_NAME + "=") for header in me.headers.get_list("set-cookie")
    )
    assert await langsmith_oauth.langsmith_access_token(session["sub"]) == "access"
    assert (await client.get("/dashboard/api/repos")).status_code == 403
    assert (await client.get("/dashboard/api/repos/example/repo/merge-methods")).status_code == 403
    assert (await client.get("/dashboard/api/reviews/example/repo/1/labels")).status_code == 403
    assert (await client.get("/dashboard/api/enabled-review-repos")).status_code == 403
    assert (await client.get("/dashboard/api/review-styles")).status_code == 403
    with pytest.raises(UnauthorizedUser):
        await user.link("github", "1", login="alice")
    with pytest.raises(UnauthorizedUser):
        await github_user.link("langsmith", server.subject)


async def test_slack_email_collision_keeps_accounts_independent(langsmith_client) -> None:
    server, client = langsmith_client
    github_user = await User.sign_in("github", "1", login="alice", email="alice@example.com")
    await github_user.link("slack", "slack-alice", email="same@example.com")
    callback = await server.desktop_login(client)
    assert callback.status_code == 302
    user = await User.for_identity("langsmith", server.subject)
    assert user is not None and user.id != github_user.id
    exchanged = await server.exchange(client, callback)
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).json()["requires_github_link"] is False
    assert (await client.get("/dashboard/api/profile")).status_code == 200


async def test_linking_github_returns_a_langsmith_person_to_their_github_account(
    langsmith_client,
) -> None:
    server, client = langsmith_client
    github_user = await User.sign_in("github", "1", login="alice", email="Same@Example.com")
    await save_credential(
        "langsmith_login",
        "alice",
        encrypted_access_token=encrypt_token("stale"),
        encrypted_refresh_token=None,
        access_token_expires_at=None,
        client_id="test-client",
        token_endpoint=ISSUER + "/oauth/token",
        account_email=None,
    )
    await USER_PREFERENCES.put("alice", {"follow_up_behavior": "queue"})
    github_thread = {"owner_login": "alice", "visibility": "private"}
    start = await client.get("/dashboard/api/auth/langsmith/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = await client.get(
        "/dashboard/api/langsmith/callback", params={"state": state, "code": "code"}
    )
    assert callback.status_code == 302
    person = await User.for_identity("langsmith", server.subject)
    assert person is not None and person.id != github_user.id
    fresh = oauth.decode_session(client.cookies[oauth.COOKIE_NAME])
    assert fresh["sub"] == f"user:{person.id}"
    with pytest.raises(HTTPException):
        Principal.of_person(fresh).assert_can_read(github_thread)

    joined = await person.join_github("1", login="alice", email="Same@Example.com")
    assert joined.id == github_user.id
    assert {identity.provider for identity in joined.identities} == {"github", "langsmith"}
    assert await User.for_identity("langsmith", server.subject) == joined
    credential = await load_credential("langsmith_login", "alice")
    assert credential is not None and decrypt_token(credential.encrypted_access_token) == "access"
    assert await load_credential("langsmith_login", f"user:{person.id}") is None
    users, total = await User.page(offset=0, limit=10)
    assert total == 1
    assert [user.id for user in users] == [joined.id]
    assert await User.get(person.id) is not None
    users, total = await User.page(offset=0, limit=10, search=person.display_name)
    assert person.id not in {user.id for user in users}

    me = await client.get("/dashboard/api/me")
    assert me.status_code == 200
    assert me.json()["github_login"] == "alice" and me.json()["login"] == "alice"
    assert me.json()["requires_github_link"] is False
    session = oauth.decode_session(me.cookies[oauth.COOKIE_NAME])
    assert (session["sub"], session["provider"]) == ("alice", "langsmith")
    assert me.json()["follow_up_behavior"] == "queue"
    Principal.of_person(session).assert_can_read(github_thread)
    assert (await client.get("/dashboard/api/review-styles")).status_code != 403

    other = await User.sign_in(
        "langsmith",
        "ls-other",
        email="other@example.com",
        authorization=LangSmithAccount(sub="ls-other", ls_org_id=server.organization),
    )
    users, total = await User.page(offset=0, limit=10)
    assert total == 2
    assert {user.id for user in users} == {joined.id, other.id}
    with pytest.raises(AccountConflict):
        await other.join_github("1", login="alice", email="Same@Example.com")


@pytest.mark.parametrize("account", ["correct", "wrong", "occupied"])
async def test_slack_recovery_requires_the_github_owner_before_moving_identities(
    langsmith_client, account: str
) -> None:
    server, client = langsmith_client
    legacy = await User.sign_in("github", "1", login="alice", email="same@example.com")
    await legacy.link("slack", "U-legacy", email="same@example.com")
    await USER_PREFERENCES.put("alice", {"follow_up_behavior": "queue"})
    if account == "occupied":
        previous = await User.sign_in(
            "langsmith",
            "previous-login",
            authorization=LangSmithAccount(sub="previous-login", ls_org_id=server.organization),
        )
        await previous.join_github("1", login="alice", email="same@example.com")
    callback = await server.desktop_login(client)
    exchanged = await server.exchange(client, callback)
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    current = await User.for_identity("langsmith", server.subject)
    assert current is not None
    if account == "correct":
        joined = await current.join_github(
            "1", login="alice", email="same@example.com", recovery_slack_id="U-legacy"
        )
        assert joined.id == legacy.id
        assert await User.for_identity("slack", "U-legacy") == joined
        assert await User.for_identity("langsmith", server.subject) == joined
        credential = await load_credential("langsmith_login", "alice")
        assert credential is not None
        assert decrypt_token(credential.encrypted_access_token) == "access"
        me = await client.get("/dashboard/api/me")
        assert me.status_code == 200 and me.json()["follow_up_behavior"] == "queue"
    else:
        with pytest.raises(AccountConflict):
            await current.join_github(
                "wrong" if account == "wrong" else "1",
                login="wrong" if account == "wrong" else "alice",
                email="same@example.com",
                recovery_slack_id="U-legacy",
            )
        assert await User.for_identity("slack", "U-legacy") == await User.get(legacy.id)
        assert await User.for_identity("github", "1") == await User.get(legacy.id)
        assert await User.for_identity("github", "wrong") is None
        assert await User.for_identity("langsmith", server.subject) == current
        assert await load_credential("langsmith_login", f"user:{current.id}") is not None


async def test_login_without_desktop_proof_and_cross_purpose_codes_are_rejected(
    langsmith_client,
) -> None:
    server, client = langsmith_client
    assert (
        await client.get("/dashboard/api/auth/langsmith/login", params={"desktop_port": 43210})
    ).status_code == 400
    state = oauth.issue_state(
        redirect_to="https://dashboard.example", nonce_hash="nonce", provider="langsmith"
    )
    callback = await client.get(
        "/dashboard/api/langsmith/callback", params={"state": state, "code": "code"}
    )
    assert callback.status_code == 400
    code = oauth.issue_connect_handoff(
        provider="langsmith",
        challenge=oauth.pkce_s256_challenge("verifier"),
        claims={"sub": "user:123", "user_id": "123"},
    )
    response = await client.post(
        "/dashboard/api/auth/desktop/exchange", json={"code": code, "verifier": "verifier"}
    )
    assert response.status_code == 400
    assert await User.for_identity("langsmith", server.subject) is None


@pytest.mark.parametrize(("rate_limited", "status"), [(2, 302), (3, 503)])
async def test_rate_limited_sign_in_retries_then_reports_unavailable(
    langsmith_client, rate_limited: int, status: int
) -> None:
    server, client = langsmith_client
    server.rate_limited = rate_limited
    callback = await server.desktop_login(client)
    assert callback.status_code == status
    if status == 503:
        assert callback.json()["detail"] == "LangSmith authorization is temporarily unavailable"


@pytest.mark.parametrize("wrong_org", [False, True])
async def test_nonmembers_get_no_login_user_or_grant(langsmith_client, wrong_org: bool) -> None:
    server, client = langsmith_client
    if wrong_org:
        server.organization = "other-org"
    else:
        server.member = False
    response = await server.desktop_login(client)
    assert response.status_code == 403
    assert await User.for_identity("langsmith", server.subject) is None
    assert oauth.COOKIE_NAME not in client.cookies


async def test_membership_is_rechecked_and_revocation_removes_access(langsmith_client) -> None:
    server, client = langsmith_client
    callback = await server.desktop_login(client)
    exchanged = await server.exchange(client, callback)
    session = oauth.decode_session(exchanged.json()["session"])
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 200
    server.unavailable = True
    assert (await client.get("/dashboard/api/me")).status_code == 200
    await server.expire_authorization(session["sub"])
    assert (await client.get("/dashboard/api/me")).status_code == 503
    assert (await client.get("/dashboard/api/me")).status_code == 503
    assert await load_credential("langsmith_login", session["sub"]) is not None
    server.unavailable = False
    server.member = False
    assert (await client.get("/dashboard/api/me")).status_code == 403
    assert await load_credential("langsmith_login", session["sub"]) is None
    assert (await client.get("/dashboard/api/me")).status_code == 401


@pytest.mark.parametrize("revoked", [False, True])
async def test_expired_login_tokens_refresh_and_revoked_grants_fail_closed(
    langsmith_client, revoked: bool
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    await save_credential(
        "langsmith_login",
        session["sub"],
        encrypted_access_token=encrypt_token("expired"),
        encrypted_refresh_token=encrypt_token("refresh"),
        access_token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        client_id="test-client",
        token_endpoint=ISSUER + "/oauth/token",
        account_email="same@example.com",
    )
    server.revoked = revoked
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    response = await client.get("/dashboard/api/me")
    assert response.status_code == (401 if revoked else 200)
    stored = await load_credential("langsmith_login", session["sub"])
    if revoked:
        assert stored is None
    else:
        assert stored is not None and decrypt_token(stored.encrypted_access_token) == "access"
        assert (await client.get("/dashboard/api/me")).status_code == 200
    assert server.refreshes == 1


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("older", [False, True])
async def test_workspace_consent_requires_interactive_sign_in(
    langsmith_client, expired: bool, older: bool
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    subject = oauth.decode_session(exchanged.json()["session"])["sub"]
    credential = await load_credential("langsmith_login", subject)
    assert credential is not None
    signed_in_at = langsmith_login.LANGSMITH_WORKSPACE_LOGIN_SINCE - timedelta(seconds=int(older))
    async with postgres.session() as session:
        await session.execute(
            update(UserIdentity)
            .where(
                UserIdentity.user_id == credential.user_id,
                UserIdentity.provider == "langsmith",
            )
            .values(last_seen_at=signed_in_at)
        )
    login = langsmith_login.LangSmithLogin()
    await login.require_configured()
    await login.save(
        subject,
        {"access_token": "access", "expires_in": 1 if expired else 3600},
        ISSUER + "/oauth/token",
        previous=credential,
        authorized_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == (401 if older else 200)
    assert server.refreshes == int(expired and not older)
    assert await langsmith_oauth.langsmith_access_token(subject) == (None if older else "access")
    if older:
        assert await load_credential("langsmith_login", subject) is not None
        fresh = await server.exchange(client, await server.desktop_login(client))
        client.cookies.set(oauth.COOKIE_NAME, fresh.json()["session"])
        assert (await client.get("/dashboard/api/me")).status_code == 200
        assert await langsmith_oauth.langsmith_access_token(subject) == "access"


async def test_incomplete_sign_in_does_not_authorize_the_previous_grant(langsmith_client) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    subject = oauth.decode_session(exchanged.json()["session"])["sub"]
    previous = await load_credential("langsmith_login", subject)
    assert previous is not None
    await User.sign_in(
        "langsmith",
        server.subject,
        authorization=LangSmithAccount(sub=server.subject, ls_org_id=ALLOWED_ORG),
    )
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 401
    assert server.refreshes == 0
    assert await load_credential("langsmith_login", subject) is not None
    fresh = await server.exchange(client, await server.desktop_login(client))
    stored = await load_credential("langsmith_login", subject)
    assert stored is not None and stored.created_at == previous.created_at
    assert not await previous.signed_in_since(langsmith_login.LANGSMITH_WORKSPACE_LOGIN_SINCE)
    assert await load_credential("langsmith_login", subject) is not None
    client.cookies.set(oauth.COOKIE_NAME, fresh.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 200


async def test_changed_provider_subject_cannot_rebind_a_session(langsmith_client) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    server.subject = "different-person"
    await server.expire_authorization(session["sub"])
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 401
    assert await load_credential("langsmith_login", session["sub"]) is None


async def test_login_requires_an_org_source_and_org_changes_invalidate_cached_access(
    langsmith_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    monkeypatch.delenv("LANGSMITH_API_KEY")
    assert (await client.get("/dashboard/api/me")).status_code == 503
    start = await client.get(
        "/dashboard/api/auth/langsmith/login",
        params={"desktop_handoff": oauth.pkce_s256_challenge("verifier"), "desktop_port": 43210},
    )
    assert start.status_code == 503
    assert langsmith_oauth.langsmith_oauth_configured()
    monkeypatch.setenv("LANGSMITH_API_KEY", "deployment-key")
    assert (await client.get("/dashboard/api/me")).status_code == 200
    server.deployment_org = "different-org"
    server.clock += 61
    assert (await client.get("/dashboard/api/me")).status_code == 403


@pytest.mark.parametrize(
    "status,body",
    [
        (403, {}),
        (503, {}),
        (302, {}),
        (200, {}),
        (200, {"organization_id": " "}),
        (200, {"organization_id": 123}),
        (200, {"organization_id": ALLOWED_ORG}),
        (200, {"organization_id": ALLOWED_ORG, "tenant_id": " "}),
        (200, {"organization_id": ALLOWED_ORG, "tenant_id": 123}),
        (200, []),
    ],
)
async def test_deployment_org_discovery_fails_closed(
    monkeypatch: pytest.MonkeyPatch, status: int, body: object
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "deployment-key")
    monkeypatch.setattr(
        langsmith_login, "LANGSMITH_ORG_DISCOVERY", langsmith_login.LangSmithOrgDiscovery()
    )
    transport = httpx2.MockTransport(lambda request: httpx2.Response(status, json=body))
    monkeypatch.setattr(langsmith_login, "pooled_transport", lambda: transport)
    with pytest.raises(HTTPException) as exc:
        await langsmith_login.LangSmithLogin().allowed_org()
    assert exc.value.status_code == 503


async def test_cached_authorization_uses_no_provider_calls_or_refresh_lock(
    langsmith_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    checks = server.authorizations
    discoveries = server.discoveries
    server.discovery_unavailable = True
    monkeypatch.setattr(langsmith_login, "refresh_guard", lambda *args: pytest.fail("refresh lock"))
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert await langsmith_oauth.langsmith_access_token(session["sub"]) == "access"
    await THREAD_PINS.add(session["sub"], {}, key="pinned")
    checkout = Mock()
    engine = postgres.engine().sync_engine
    event.listen(engine, "checkout", checkout)
    try:
        login = langsmith_login.LangSmithLogin()
        async with login.authorized_read(DashboardPrincipal.from_session(session)) as db:
            assert await THREAD_PINS.ordered_ids(session["sub"], session=db) == ["pinned"]
        assert checkout.call_count == 1
        checkout.reset_mock()
        assert (await client.get("/dashboard/api/me")).status_code == 200
        assert checkout.call_count == 1
    finally:
        event.remove(engine, "checkout", checkout)
    assert server.authorizations == checks
    assert server.discoveries == discoveries
    credential = await load_credential("langsmith_login", session["sub"])
    assert credential is not None and credential.login_authorized_until is not None
    assert credential.login_authorized_until <= datetime.now(UTC) + timedelta(seconds=60)
    await server.expire_authorization(session["sub"])
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert server.authorizations == checks + 1
    server.clock += 61
    assert (await client.get("/dashboard/api/me")).status_code == 503
    server.discovery_unavailable = False
    assert (await client.get("/dashboard/api/me")).status_code == 503
    assert server.discoveries == discoveries + 1
    server.clock += 3
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert server.discoveries == discoveries + 2


@pytest.mark.parametrize("unavailable", [False, True])
@pytest.mark.parametrize("github_session", [False, True])
async def test_logout_revokes_replayed_sessions_and_runtime_grants(
    langsmith_client, unavailable: bool, github_session: bool
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    token = exchanged.json()["session"]
    session = oauth.decode_session(token)
    if github_session:
        user = await User.for_identity("langsmith", server.subject)
        assert user is not None
        user = await user.join_github("1", login="alice", email="same@example.com")
        token = oauth.issue_session(
            login="alice", email="same@example.com", avatar_url=None, user_id=str(user.id)
        )
    client.cookies.set(oauth.COOKIE_NAME, token, domain="dashboard.example", path="/")
    server.unavailable = unavailable
    response = await client.post("/dashboard/api/auth/logout")
    assert response.status_code == 204 and oauth.COOKIE_NAME not in client.cookies
    assert server.revocations == ([] if unavailable else ["refresh", "access"])
    assert await load_credential("langsmith_login", session["sub"]) is None
    client.cookies.set(oauth.COOKIE_NAME, token)
    assert (await client.get("/dashboard/api/me")).status_code == 401
    assert await langsmith_oauth.langsmith_access_token(session["sub"]) is None
    if github_session:
        assert await langsmith_oauth.langsmith_access_token("alice") is None
    assert (await client.post("/dashboard/api/auth/logout")).status_code == 204


@pytest.mark.parametrize("replace_grant", [False, True])
async def test_inflight_authorization_cannot_restore_or_delete_a_replaced_grant(
    langsmith_client, replace_grant: bool
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    await server.expire_authorization(session["sub"])
    server.checking_membership, server.resume_membership = asyncio.Event(), asyncio.Event()
    request = asyncio.create_task(client.get("/dashboard/api/me"))
    try:
        await asyncio.wait_for(server.checking_membership.wait(), timeout=5)
        if replace_grant:
            await langsmith_login.LangSmithLogin().save(
                session["sub"],
                {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
                ISSUER + "/oauth/token",
            )
        else:
            assert (await client.post("/dashboard/api/auth/logout")).status_code == 204
    finally:
        server.resume_membership.set()
    assert (await request).status_code == 401
    stored = await load_credential("langsmith_login", session["sub"])
    assert (stored is not None) == replace_grant
    if replace_grant:
        assert (await client.get("/dashboard/api/me")).status_code == 200


async def test_refreshed_identity_mismatch_removes_the_rotated_grant(langsmith_client) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    await save_credential(
        "langsmith_login",
        session["sub"],
        encrypted_access_token=encrypt_token("expired"),
        encrypted_refresh_token=encrypt_token("refresh"),
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        client_id="test-client",
        token_endpoint=ISSUER + "/oauth/token",
        account_email="same@example.com",
    )
    server.subject = "different-person"
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 401
    assert server.refreshes == 1
    assert await load_credential("langsmith_login", session["sub"]) is None


async def test_refresh_survives_transient_membership_failure(langsmith_client) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    await save_credential(
        "langsmith_login",
        session["sub"],
        encrypted_access_token=encrypt_token("expired"),
        encrypted_refresh_token=encrypt_token("refresh"),
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        client_id="test-client",
        token_endpoint=ISSUER + "/oauth/token",
        account_email="same@example.com",
    )
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    server.unavailable = True
    assert (await client.get("/dashboard/api/me")).status_code == 503
    stored = await load_credential("langsmith_login", session["sub"])
    assert stored is not None
    assert decrypt_token(stored.encrypted_access_token) == "access"
    assert stored.login_authorized_until is None
    assert server.refreshes == 1
    server.unavailable = False
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert server.refreshes == 1


@pytest.mark.parametrize("setting", ["LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT"])
async def test_deployment_org_cache_is_bound_to_key_and_endpoint(
    langsmith_client, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    server, client = langsmith_client
    assert await langsmith_login.LangSmithLogin().allowed_org() == ALLOWED_ORG
    server.discovery_unavailable = True
    monkeypatch.setenv(
        setting, "changed-value" if setting == "LANGSMITH_API_KEY" else "https://other.example"
    )
    assert (await client.get("/dashboard/api/auth/langsmith/login")).status_code == 503


@pytest.mark.parametrize("unavailable", [False, True])
async def test_concurrent_org_discovery_reuses_one_lookup(
    langsmith_client, unavailable: bool
) -> None:
    server, _ = langsmith_client
    server.discovery_unavailable = unavailable
    results = await asyncio.gather(
        *(langsmith_login.LangSmithLogin().allowed_org() for _ in range(10)),
        return_exceptions=True,
    )
    if unavailable:
        assert all(
            isinstance(result, HTTPException) and result.status_code == 503 for result in results
        )
    else:
        assert results == [ALLOWED_ORG] * 10
    assert server.discoveries == 1


@pytest.mark.parametrize("revoked", [False, True])
async def test_a_run_calls_models_as_the_person_who_started_it(
    langsmith_client, revoked: bool
) -> None:
    """A run a person started goes through the gateway on their login, refreshed as it
    nears expiry, and never falls back to the deployment's key once that login is gone.
    One only attributed to them (a wakeup, an event match) bills the deployment."""
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    subject = oauth.decode_session(exchanged.json()["session"])["sub"]
    await save_credential(
        "langsmith_login",
        subject,
        encrypted_access_token=encrypt_token("expired"),
        encrypted_refresh_token=encrypt_token("refresh"),
        access_token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        client_id="test-client",
        token_endpoint=ISSUER + "/oauth/token",
        account_email="same@example.com",
    )
    server.revoked = revoked
    user_id = subject.removeprefix("user:")
    person: RunnableConfig = {"metadata": {"user_id": user_id, SENDER_USER_KEY: user_id}}
    attributed: RunnableConfig = {"metadata": {"user_id": user_id}}

    credential = await langsmith_oauth.LangSmithModelAccess.for_run(person).credential()

    if revoked:
        assert credential == GatewayCredential(
            unavailable=langsmith_oauth.LangSmithModelAccess.SIGN_IN_AGAIN
        )
    else:
        assert credential == GatewayCredential(token="access")
    assert await langsmith_oauth.LangSmithModelAccess.for_run(attributed).credential() == (
        GatewayCredential()
    )
