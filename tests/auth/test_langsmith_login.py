"""Independent LangSmith login, membership revocation, and desktop handoff."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException

from openswe.dashboard import auth_routes, langsmith_login, langsmith_oauth, langsmith_routes, oauth
from openswe.dashboard.oauth_credentials import load_credential, save_credential
from openswe.dashboard.profiles import PROFILES, get_profile
from openswe.dashboard.user_preferences import USER_PREFERENCES
from openswe.encryption import decrypt_token, encrypt_token
from openswe.github import dashboard_routes
from openswe.review.routes import router as review_router
from openswe.users import UnauthorizedUser, User

ISSUER = "https://api.smith.langchain.com"
ALLOWED_ORG = "f5c798a2-2155-4999-ad27-6d466bd26e1c"


@dataclass
class LangSmithServer:
    subject: str = "5bb3fb19-f328-4815-8fe8-0483b41312c2"
    organization: str = ALLOWED_ORG
    member: bool = True
    revoked: bool = False
    unavailable: bool = False
    refreshes: int = 0
    authorizations: int = 0
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
            body = parse_qs(request.content.decode())
            assert body["resource"] == [ISSUER]
            assert body["client_secret"] == ["test-client-secret"]
            if body["grant_type"] == ["refresh_token"]:
                self.refreshes += 1
                if self.revoked:
                    return httpx2.Response(400, json={"error": "invalid_grant"})
            else:
                assert body["code_verifier"][0]
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
        callback = await client.get(
            "/dashboard/api/auth/langsmith/callback",
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
    monkeypatch.setenv("LANGSMITH_LOGIN_ORG_ID", ALLOWED_ORG)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    server = LangSmithServer()
    monkeypatch.setattr(
        langsmith_login, "pooled_transport", lambda: httpx2.MockTransport(server.respond)
    )
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/dashboard/api")
    app.include_router(langsmith_routes.router, prefix="/dashboard/api")
    app.include_router(dashboard_routes.router, prefix="/dashboard/api")
    app.include_router(review_router, prefix="/dashboard/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://dashboard.example"
    ) as client:
        yield server, client


async def test_desktop_login_is_independent_and_pkce_bound(langsmith_client) -> None:
    server, client = langsmith_client
    github_user = await User.sign_in("github", "1", login="alice", email="same@example.com")
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
    email_user = await User.for_email("same@example.com")
    assert email_user is not None and email_user.id == github_user.id
    assert (await User.canonical_person({"id": session["sub"]}))["id"] == session["sub"]
    await PROFILES.put(session["sub"], {"draft_prs": False})
    await USER_PREFERENCES.put(session["sub"], {"follow_up_behavior": "queue"})
    profile = await get_profile(session["sub"])
    assert profile is not None and profile["draft_prs"] is False
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    me = await client.get("/dashboard/api/me")
    assert me.status_code == 200
    assert me.json()["github_login"] is None and me.json()["auth_provider"] == "langsmith"
    assert me.json()["follow_up_behavior"] == "queue" and not me.json()["is_admin"]
    assert any(
        header.startswith(oauth.COOKIE_NAME + "=") for header in me.headers.get_list("set-cookie")
    )
    assert await langsmith_oauth.langsmith_access_token(session["sub"]) == "access"
    status = await client.get("/dashboard/api/my-credentials/langsmith")
    assert status.json()["connected"] is True
    assert (await client.delete("/dashboard/api/my-credentials/langsmith")).status_code == 400
    assert (await client.get("/dashboard/api/repos")).status_code == 403
    assert (await client.get("/dashboard/api/repos/example/repo/merge-methods")).status_code == 403
    assert (await client.get("/dashboard/api/reviews/example/repo/1/labels")).status_code == 403
    assert (await client.get("/dashboard/api/enabled-review-repos")).status_code == 403
    assert (await client.get("/dashboard/api/review-styles")).status_code == 403
    with pytest.raises(UnauthorizedUser):
        await user.link("github", "1", login="alice")
    with pytest.raises(UnauthorizedUser):
        await github_user.link("langsmith", server.subject)


async def test_login_without_desktop_proof_and_cross_purpose_codes_are_rejected(
    langsmith_client,
) -> None:
    server, client = langsmith_client
    assert (await client.get("/dashboard/api/auth/langsmith/login")).status_code == 400
    state = oauth.issue_state(
        redirect_to="https://dashboard.example", nonce_hash="nonce", provider="langsmith"
    )
    callback = await client.get(
        "/dashboard/api/auth/langsmith/callback", params={"state": state, "code": "code"}
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


async def test_changed_provider_subject_cannot_rebind_a_session(langsmith_client) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    server.subject = "different-person"
    await server.expire_authorization(session["sub"])
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 401
    assert await load_credential("langsmith_login", session["sub"]) is None


async def test_login_is_opt_in_and_org_changes_invalidate_cached_access(
    langsmith_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    monkeypatch.delenv("LANGSMITH_LOGIN_ORG_ID")
    assert (await client.get("/dashboard/api/me")).status_code == 503
    start = await client.get(
        "/dashboard/api/auth/langsmith/login",
        params={"desktop_handoff": oauth.pkce_s256_challenge("verifier"), "desktop_port": 43210},
    )
    assert start.status_code == 503
    assert langsmith_oauth.langsmith_oauth_configured()
    monkeypatch.setenv("LANGSMITH_LOGIN_ORG_ID", "different-org")
    assert (await client.get("/dashboard/api/me")).status_code == 403


async def test_cached_authorization_uses_no_provider_calls_or_refresh_lock(
    langsmith_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    session = oauth.decode_session(exchanged.json()["session"])
    checks = server.authorizations
    monkeypatch.setattr(langsmith_login, "refresh_guard", lambda *args: pytest.fail("refresh lock"))
    client.cookies.set(oauth.COOKIE_NAME, exchanged.json()["session"])
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert await langsmith_oauth.langsmith_access_token(session["sub"]) == "access"
    assert server.authorizations == checks
    credential = await load_credential("langsmith_login", session["sub"])
    assert credential is not None and credential.login_authorized_until is not None
    assert credential.login_authorized_until <= datetime.now(UTC) + timedelta(seconds=60)
    await server.expire_authorization(session["sub"])
    assert (await client.get("/dashboard/api/me")).status_code == 200
    assert server.authorizations == checks + 1


@pytest.mark.parametrize("unavailable", [False, True])
async def test_logout_revokes_replayed_sessions_and_runtime_grants(
    langsmith_client, unavailable: bool
) -> None:
    server, client = langsmith_client
    exchanged = await server.exchange(client, await server.desktop_login(client))
    token = exchanged.json()["session"]
    session = oauth.decode_session(token)
    client.cookies.set(oauth.COOKIE_NAME, token, domain="dashboard.example", path="/")
    server.unavailable = unavailable
    response = await client.post("/dashboard/api/auth/logout")
    assert response.status_code == 204 and oauth.COOKIE_NAME not in client.cookies
    assert server.revocations == ([] if unavailable else ["refresh", "access"])
    assert await load_credential("langsmith_login", session["sub"]) is None
    client.cookies.set(oauth.COOKIE_NAME, token)
    assert (await client.get("/dashboard/api/me")).status_code == 401
    with pytest.raises(HTTPException, match="LangSmith login expired"):
        await langsmith_oauth.langsmith_access_token(session["sub"])
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
