import json
from typing import Any

import pytest

import openswe.utils.authorship as authorship
from openswe.github import app as github_app
from openswe.users import User
from openswe.utils import ttl_cache
from openswe.utils.authorship import (
    resolve_participant_identities,
    resolve_public_github_profile,
    resolve_triggering_user_identity,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_slack_id", ["", "U_CURRENT"])
@pytest.mark.parametrize(
    ("run_login", "canonical_found"), [("mason", True), ("", True), ("mason", False)]
)
async def test_participant_context_resolves_canonical_identity(
    monkeypatch: pytest.MonkeyPatch,
    source_slack_id: str,
    run_login: str,
    canonical_found: bool,
) -> None:
    from unittest.mock import AsyncMock

    from openswe.input_messages import person_introduction
    from openswe.server import _thread_participant
    from openswe.users.models import UserIdentity

    user = User(
        display_name="Mason",
        identities=[
            UserIdentity(provider="slack", external_id="U_LINKED"),
            UserIdentity(provider="github", external_id="123", login="mason"),
        ],
    )
    monkeypatch.setattr(User, "get", AsyncMock(return_value=user if canonical_found else None))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=user))
    monkeypatch.setattr("openswe.server.load_profile", AsyncMock(return_value={}))
    monkeypatch.setattr("openswe.server.participant_is_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "openswe.server._resolve_user_custom_instructions", AsyncMock(return_value="")
    )
    participant = await _thread_participant(
        authorship.CollaboratorIdentity(
            display_name="Mason",
            commit_name="Mason",
            commit_email="mason@example.com",
            github_login=run_login,
        ),
        {},
        person_id=f"user:{user.id}",
        slack_user_id=source_slack_id,
    )
    content = person_introduction(participant.as_person())["content"]
    assert "open_swe_account: linked" in content
    assert "github_login: mason" in content
    assert "commit_email: mason@users.noreply.github.com" in content
    assert f"slack_user_id: {source_slack_id or 'U_LINKED'}" in content


async def test_participant_identity_ignores_users_table_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def other_email(login: str | None) -> str:
        return "mason@work.example"

    monkeypatch.setattr(User, "email_for_login", other_email)
    identities = await resolve_participant_identities(["mason-gh", " ", "mason-gh"])
    assert [identity.commit_email for identity in identities] == [
        "mason-gh@users.noreply.github.com"
    ]


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise json.JSONDecodeError("empty", "", 0)
        return self._payload


class _FakeAsyncClient:
    """Captures GET requests and replays queued responses."""

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str], **kwargs: Any) -> _FakeResponse:
        self.requests.append((url, headers))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def github_client(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncClient:
    client = _FakeAsyncClient([])
    monkeypatch.setattr(authorship.httpx2, "AsyncClient", lambda *a, **kw: client)
    return client


@pytest.fixture
def installation_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_token(**kwargs: Any) -> str:
        return "installation-token"

    monkeypatch.setattr(github_app, "get_github_app_installation_token", fake_token)


@pytest.fixture(autouse=True)
def _clear_public_profile_cache() -> None:
    ttl_cache.clear()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 0, "login": "mason-gh", "name": "Mason"},
        {"id": -3, "login": "mason-gh", "name": "Mason"},
        {"id": "42", "login": "mason-gh", "name": "Mason"},
        {"id": True, "login": "mason-gh", "name": "Mason"},
        {"id": 42, "login": "someone-else", "name": "Mason"},
        {"id": 42, "login": "mason-gh", "name": 7},
    ],
)
async def test_public_profile_lookup_rejects_invalid_payloads(
    payload: dict[str, Any], github_client: _FakeAsyncClient, installation_token: None
) -> None:
    github_client._responses.append(_FakeResponse(200, payload))
    assert await resolve_public_github_profile("mason-gh") is None


@pytest.mark.parametrize("login", ["", "   ", "a" * 40, "-bad", "bad-", "has space", "a/b"])
async def test_public_profile_lookup_rejects_invalid_logins(
    login: str, github_client: _FakeAsyncClient, installation_token: None
) -> None:
    assert await resolve_public_github_profile(login) is None
    assert github_client.requests == []


async def test_config_identity_without_trusted_slack_name_stays_blank(
    github_client: _FakeAsyncClient, installation_token: None
) -> None:
    github_client._responses.append(_FakeResponse(404, {"message": "Not Found"}))
    config = {
        "configurable": {
            "source": "linear",
            "github_login": "mason-gh",
            "linear_issue": {"triggering_user_name": "Linear Mason"},
        }
    }
    identity = await resolve_triggering_user_identity(config)
    assert identity is not None
    assert identity.analytics_display_name == ""
    assert identity.display_name_source is None
    assert identity.commit_name == "Linear Mason"


async def test_installation_token_identity_skips_the_user_endpoint(
    github_client: _FakeAsyncClient, installation_token: None
) -> None:
    github_client._responses.append(
        _FakeResponse(200, {"id": 42, "login": "mason-gh", "name": "Mason"})
    )
    config = {"configurable": {"source": "slack", "github_login": "mason-gh"}}

    identity = await resolve_triggering_user_identity(config, "ghs_workspacebot")

    assert identity is not None
    assert identity.github_login == "mason-gh"
    assert [url for url, _ in github_client.requests] == ["https://api.github.com/users/mason-gh"]


async def test_user_token_identity_is_shared_until_it_expires_but_failures_are_not(
    github_client: _FakeAsyncClient,
) -> None:
    github_client._responses.extend(
        [
            _FakeResponse(401, {"message": "Bad credentials"}),
            _FakeResponse(200, {"id": 7, "login": "mason-gh", "name": "Mason"}),
        ]
    )
    no_login: dict[str, Any] = {"configurable": {}}

    assert await resolve_triggering_user_identity(no_login, "gho_user") is None
    first = await resolve_triggering_user_identity(no_login, "gho_user")
    second = await resolve_triggering_user_identity(no_login, "gho_user")

    assert first is not None and first.github_login == "mason-gh"
    assert second == first
    assert [url for url, _ in github_client.requests] == ["https://api.github.com/user"] * 2
