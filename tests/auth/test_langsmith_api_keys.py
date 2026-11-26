"""LangSmith API keys as Robin credentials: who LangSmith says a key is, and who that is here."""

from dataclasses import dataclass, field

import httpx
import httpx2
import pytest
from fastapi import FastAPI, HTTPException

from openswe.dashboard import authentication, langsmith_api_keys
from openswe.dashboard.langsmith_api_keys import LANGSMITH_API_KEYS, LangSmithApiKey
from openswe.threads.principals import Principal, PrincipalDep
from tests.conftest import LangSmithUsers

ORG = "org-robin"
WORKSPACE = "ws-robin"
PAT = "lsv2_pt_ada"
SERVICE_KEY = "lsv2_sk_ci"


class _Discovery:
    async def resolve(self) -> tuple[str, str]:
        return ORG, WORKSPACE


@dataclass
class _LangSmith:
    """The tenant ``/auth`` endpoint as LangSmith answers it for the keys it knows."""

    keys: dict[str, dict[str, object]] = field(default_factory=dict)
    status: int | None = None
    calls: list[httpx2.Request] = field(default_factory=list)

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        assert request.url.path == "/auth"
        if self.status is not None:
            return httpx2.Response(self.status, json={"error": "upstream"})
        identity = self.keys.get(request.headers.get("X-Api-Key", ""))
        if identity is None or identity["tenant_id"] != request.headers.get("X-Tenant-Id"):
            return httpx2.Response(403, json={"error": "Forbidden"})
        return httpx2.Response(200, json=identity)


@pytest.fixture
def langsmith(monkeypatch: pytest.MonkeyPatch) -> _LangSmith:
    server = _LangSmith(
        keys={
            PAT: {
                "organization_id": ORG,
                "tenant_id": WORKSPACE,
                "user_id": "supabase-ada",
                "ls_user_id": "ls-ada",
                "user_email": "ada@example.com",
                "api_key_id": "pat-1",
                "identity_permissions": ["runs:read"],
            },
            SERVICE_KEY: {
                "organization_id": ORG,
                "tenant_id": WORKSPACE,
                "api_key_id": "sk-1",
                "identity_permissions": ["runs:read", "workspaces:manage"],
            },
        }
    )
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://langsmith.test")
    monkeypatch.setattr(langsmith_api_keys, "LANGSMITH_ORG_DISCOVERY", _Discovery())
    monkeypatch.setattr(
        langsmith_api_keys, "pooled_transport", lambda: httpx2.MockTransport(server.respond)
    )
    return server


async def test_a_personal_access_token_is_its_langsmith_user(langsmith: _LangSmith) -> None:
    key = await LANGSMITH_API_KEYS.verify(PAT)

    assert key is not None
    assert (key.kind, key.ls_user_id, key.email) == ("personal", "ls-ada", "ada@example.com")
    assert key.workspace_id == WORKSPACE
    assert key.audit_id == "langsmith:pat-1"
    assert PAT not in repr(key)
    (call,) = langsmith.calls
    assert call.headers["X-Tenant-Id"] == WORKSPACE


async def test_a_service_key_is_no_person(langsmith: _LangSmith) -> None:
    key = await LANGSMITH_API_KEYS.verify(SERVICE_KEY)

    assert key is not None
    assert (key.kind, key.ls_user_id) == ("service", "")


async def test_a_service_key_without_workspace_admin_is_not_the_bot(langsmith: _LangSmith) -> None:
    langsmith.keys["lsv2_sk_tracing"] = {
        "organization_id": ORG,
        "tenant_id": WORKSPACE,
        "identity_permissions": ["runs:create", "runs:read"],
    }

    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify("lsv2_sk_tracing")

    assert refused.value.status_code == 403
    assert "workspaces:manage" in str(refused.value.detail)


async def test_robins_own_keys_never_reach_langsmith(
    langsmith: _LangSmith, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", SERVICE_KEY)

    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify(SERVICE_KEY)

    assert refused.value.status_code == 403
    assert langsmith.calls == []


@pytest.mark.parametrize(
    "token", ["osk_old_robin_key", "lsv2_tr_tracing", "ls__legacy", "ghs_github", "eyJ.a.b"]
)
async def test_other_tokens_are_not_langsmith_keys_and_never_reach_langsmith(
    langsmith: _LangSmith, token: str
) -> None:
    assert await LANGSMITH_API_KEYS.verify(token) is None
    assert langsmith.calls == []


async def test_a_key_from_another_workspace_is_refused(langsmith: _LangSmith) -> None:
    # LangSmith answers for the workspace the key is pinned to, not the one asked for.
    langsmith.keys["lsv2_sk_elsewhere"] = {
        "organization_id": ORG,
        "tenant_id": "ws-other",
        "identity_permissions": ["workspaces:manage"],
    }

    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify("lsv2_sk_elsewhere")

    assert refused.value.status_code == 401


async def test_an_answer_for_another_workspace_is_refused(
    langsmith: _LangSmith, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ignores_the_pin(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "organization_id": ORG,
                "tenant_id": "ws-other",
                "identity_permissions": ["workspaces:manage"],
            },
        )

    monkeypatch.setattr(
        langsmith_api_keys, "pooled_transport", lambda: httpx2.MockTransport(ignores_the_pin)
    )

    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify(SERVICE_KEY)

    assert refused.value.status_code == 401


async def test_an_unknown_key_is_refused(langsmith: _LangSmith) -> None:
    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify("lsv2_pt_unknown")

    assert refused.value.status_code == 401


async def test_a_key_whose_identity_contradicts_its_prefix_is_refused(
    langsmith: _LangSmith,
) -> None:
    langsmith.keys["lsv2_pt_nobody"] = {"organization_id": ORG, "tenant_id": WORKSPACE}

    with pytest.raises(HTTPException) as refused:
        await LANGSMITH_API_KEYS.verify("lsv2_pt_nobody")

    assert refused.value.status_code == 401


async def test_langsmith_being_down_is_not_a_refusal(langsmith: _LangSmith) -> None:
    langsmith.status = 502

    with pytest.raises(HTTPException) as unavailable:
        await LANGSMITH_API_KEYS.verify(SERVICE_KEY)

    assert unavailable.value.status_code == 503


async def test_a_verified_key_is_reused_but_a_refusal_is_not(langsmith: _LangSmith) -> None:
    await LANGSMITH_API_KEYS.verify(SERVICE_KEY)
    await LANGSMITH_API_KEYS.verify(SERVICE_KEY)
    for _ in range(2):
        with pytest.raises(HTTPException):
            await LANGSMITH_API_KEYS.verify("lsv2_sk_unknown")

    assert len(langsmith.calls) == 3


def _app() -> FastAPI:
    application = FastAPI()

    @application.get("/dashboard/api/threads/whoami")
    async def whoami(principal: PrincipalDep) -> dict[str, object]:
        return {
            "kind": principal.kind,
            "login": principal.login,
            "email": principal.email,
            "started_by_id": principal.started_by_id,
        }

    @application.get("/dashboard/api/settings/admin-only")
    async def settings(principal: PrincipalDep) -> dict[str, object]:
        return {"kind": principal.kind}

    return application


async def _get(path: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        return await client.get(path, headers=headers)


async def test_a_service_key_is_the_deployments_bot(langsmith: _LangSmith) -> None:
    response = await _get(
        "/dashboard/api/threads/whoami", {"Authorization": f"Bearer {SERVICE_KEY}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "bot",
        "login": None,
        "email": None,
        "started_by_id": f"bot:{WORKSPACE}",
    }


async def test_every_service_key_is_the_same_bot() -> None:
    def key(fingerprint: str) -> LangSmithApiKey:
        return LangSmithApiKey(
            kind="service",
            fingerprint=fingerprint,
            organization_id=ORG,
            workspace_id=WORKSPACE,
            audit_id=f"langsmith:{fingerprint}",
        )

    old, rotated = Principal.of_bot(key("old")), Principal.of_bot(key("new"))

    old.assert_can_read({"started_by_id": rotated.started_by_id})


async def test_a_personal_access_token_acts_as_the_person_who_signed_in(
    langsmith: _LangSmith, langsmith_users: LangSmithUsers
) -> None:
    # Seeded with the LangSmith identity ls-ada: the OIDC sub sign-in stores is the ls_user_id.
    langsmith_users.add("ada")

    response = await _get("/dashboard/api/threads/whoami", {"X-Api-Key": PAT})

    assert response.status_code == 200
    assert response.json()["kind"] == "person"
    assert response.json()["login"] == "ada"
    assert response.json()["email"] == "ada@example.com"


async def test_a_personal_access_token_needs_a_robin_sign_in_first(
    langsmith: _LangSmith, registry_db: None
) -> None:
    response = await _get("/dashboard/api/threads/whoami", {"Authorization": f"Bearer {PAT}"})

    assert response.status_code == 403
    assert "Sign in to Robin" in response.json()["detail"]


async def test_a_personal_access_token_stops_when_its_persons_login_does(
    langsmith: _LangSmith, langsmith_users: LangSmithUsers
) -> None:
    ada = langsmith_users.add("ada")
    langsmith_users.revoked.add(ada.id)

    response = await _get("/dashboard/api/threads/whoami", {"X-Api-Key": PAT})

    assert response.status_code == 403
    assert "Sign in to Robin" in response.json()["detail"]


async def test_a_personal_access_token_gets_only_what_its_person_may_reach(
    langsmith: _LangSmith, langsmith_users: LangSmithUsers
) -> None:
    unlinked = langsmith_users.add("user:ada")
    unlinked.identities[0].external_id = "ls-ada"

    response = await _get("/dashboard/api/settings/admin-only", {"X-Api-Key": PAT})

    assert response.status_code == 403
    assert "linked GitHub account" in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer lsv2_sk_unknown"},
        {"Authorization": "Bearer agent-key"},
        {"Cookie": "osw_session=invalid"},
    ],
)
async def test_thread_authentication_failures_include_a_bearer_challenge(
    langsmith: _LangSmith, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")
    langsmith.status = 401
    monkeypatch.setattr(authentication, "LANGSMITH_ORG_DISCOVERY", _Discovery())
    monkeypatch.setattr(
        authentication, "pooled_transport", lambda: httpx2.MockTransport(langsmith.respond)
    )
    response = await _get("/dashboard/api/threads/whoami", headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
