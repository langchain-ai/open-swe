"""A second domain (DASHBOARD_ALIAS_URLS) answers sign-in, MCP and OIDC as itself."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe.api.public_origin import PublicOriginMiddleware
from openswe.dashboard.oauth import allowed_dashboard_origins, sanitize_redirect_to
from openswe.federation import github_oidc
from openswe.utils import dashboard_links
from openswe.utils.dashboard_links import PUBLIC_ORIGIN_HEADER

CANONICAL = "https://openswe.example"
ALIAS = "https://robin.example"


@pytest.fixture(autouse=True)
def domains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", CANONICAL)
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", CANONICAL)
    monkeypatch.setenv("DASHBOARD_ALIAS_URLS", f"{ALIAS}/, https://ROBIN-staging.example:443")
    monkeypatch.delenv("GITHUB_OIDC_AUDIENCE", raising=False)


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        (ALIAS, ALIAS),
        ("https://Robin.Example:443/", ALIAS),
        ("https://robin-staging.example", "https://robin-staging.example"),
        (CANONICAL, None),
        ("http://robin.example", None),
        ("https://evil.example", None),
        ("", None),
        (None, None),
    ],
)
def test_only_listed_aliases_are_honoured(claimed: str | None, expected: str | None) -> None:
    assert dashboard_links.PublicOrigin.alias(claimed) == expected


def _echo_app() -> TestClient:
    app = FastAPI()

    @app.get("/urls")
    def urls() -> dict[str, str]:
        return {
            "base": dashboard_links.PublicOrigin.dashboard_base_url(),
            "api": dashboard_links.PublicOrigin.dashboard_api_base_url(),
            "link": dashboard_links.dashboard_thread_url("t1") or "",
            "after_login": sanitize_redirect_to("/agents"),
        }

    app.add_middleware(PublicOriginMiddleware)
    return TestClient(app)


def test_a_request_through_the_alias_is_answered_on_it() -> None:
    body = _echo_app().get("/urls", headers={PUBLIC_ORIGIN_HEADER: ALIAS}).json()

    assert body == {
        "base": ALIAS,
        "api": ALIAS,
        # Links meant for elsewhere don't depend on who triggered them.
        "link": f"{CANONICAL}/agents/t1",
        "after_login": f"{ALIAS}/agents",
    }


@pytest.mark.parametrize("headers", [{}, {PUBLIC_ORIGIN_HEADER: "https://evil.example"}])
def test_other_requests_keep_the_canonical_domain(headers: dict[str, str]) -> None:
    body = _echo_app().get("/urls", headers=headers).json()

    assert (body["base"], body["api"], body["after_login"]) == (
        CANONICAL,
        CANONICAL,
        f"{CANONICAL}/agents",
    )
    assert dashboard_links.PublicOrigin.dashboard_base_url() == CANONICAL


def test_aliases_are_allowed_origins() -> None:
    assert {CANONICAL, ALIAS} <= allowed_dashboard_origins()
    assert sanitize_redirect_to(f"{ALIAS}/agents/t1") == f"{ALIAS}/agents/t1"


def test_oidc_accepts_the_canonical_audience_first_then_the_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert github_oidc.audiences() == [CANONICAL, "https://robin-staging.example", ALIAS]

    monkeypatch.setenv("GITHUB_OIDC_AUDIENCE", ALIAS)
    assert github_oidc.audiences() == [ALIAS, "https://robin-staging.example"]


def test_mcp_signs_in_and_verifies_on_the_domain_it_was_reached_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastmcp.server.auth.providers.github import GitHubProvider
    from key_value.aio.stores.memory import MemoryStore

    from openswe.api import app as app_module
    from openswe.dashboard.oauth import AUTH_PATH, GITHUB_CALLBACK_PATH
    from openswe.mcp import server

    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    monkeypatch.delenv("DASHBOARD_DEV_SERVER_URL", raising=False)

    def auth(api_base_url: str) -> GitHubProvider:
        return GitHubProvider(
            client_id="test-client",
            client_secret="test-secret",
            base_url=api_base_url + AUTH_PATH,
            resource_base_url=api_base_url + server.PREFIX,
            redirect_path=GITHUB_CALLBACK_PATH.removeprefix(AUTH_PATH),
            client_storage=MemoryStore(),
        )

    monkeypatch.setattr(server, "_auth", auth)
    mount = server.build_mount()
    assert mount is not None
    assert len(mount.apps) == 3
    monkeypatch.setattr(app_module, "AGENT_MCP", mount)
    client = TestClient(app_module.create_app())

    def metadata(origin: str | None) -> dict[str, object]:
        headers = {PUBLIC_ORIGIN_HEADER: origin} if origin else {}
        response = client.get("/.well-known/oauth-protected-resource/oswe/mcp", headers=headers)
        assert response.status_code == 200
        return response.json()

    for origin, expected in [
        (None, CANONICAL),
        (ALIAS, ALIAS),
        ("https://evil.example", CANONICAL),
    ]:
        document = metadata(origin)
        assert document["resource"] == f"{expected}/oswe/mcp"
        assert document["authorization_servers"] == [f"{expected}{AUTH_PATH}"]

    challenge = client.post(
        "/oswe/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={PUBLIC_ORIGIN_HEADER: ALIAS},
    )
    assert challenge.status_code == 401
    assert (
        f"{ALIAS}/.well-known/oauth-protected-resource/oswe/mcp"
        in (challenge.headers["www-authenticate"])
    )
