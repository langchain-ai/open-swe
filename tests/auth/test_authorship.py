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
async def test_participant_context_includes_slack_identity(
    monkeypatch: pytest.MonkeyPatch, source_slack_id: str
) -> None:
    from unittest.mock import AsyncMock

    from openswe.input_messages import person_introduction
    from openswe.server import _thread_participant
    from openswe.users.models import UserIdentity

    user = User(
        display_name="Mason",
        identities=[UserIdentity(provider="slack", external_id="U_LINKED")],
    )
    monkeypatch.setattr("openswe.server._user_for_login", AsyncMock(return_value=user))
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
            github_login="mason",
        ),
        {},
        slack_user_id=source_slack_id,
    )
    content = person_introduction(participant.as_person())["content"]
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
