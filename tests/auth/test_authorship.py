import json
import logging
from typing import Any

import httpx2
import pytest

import agent.utils.authorship as authorship
from agent.github import app as github_app
from agent.users import User
from agent.utils import ttl_cache
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    add_bot_coauthor_trailer,
    build_pr_attribution_footer,
    resolve_participant_identities,
    resolve_public_github_profile,
    resolve_triggering_user_identity,
)
from tests.conftest import FakeStore

_BOT_TRAILER = f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>"


def test_add_bot_coauthor_trailer_appends_bot() -> None:
    result = add_bot_coauthor_trailer("fix: thing")
    assert result == f"fix: thing\n\n{_BOT_TRAILER}"


def test_add_bot_coauthor_trailer_is_idempotent() -> None:
    once = add_bot_coauthor_trailer("fix: thing")
    assert add_bot_coauthor_trailer(once) == once


def test_build_pr_attribution_footer_includes_model_details() -> None:
    assert build_pr_attribution_footer(
        "https://openswe.vercel.app/agents/abc-123",
        model_id="openai:gpt-5.6-luna",
        reasoning_effort="xhigh",
    ) == (
        "Made by [Open SWE](https://github.com/langchain-ai/open-swe)"
        " · [view thread](https://openswe.vercel.app/agents/abc-123)"
        " · openai:gpt-5.6-luna (xhigh)"
    )


async def test_resolve_identity_from_config_uses_user_noreply_email() -> None:
    config = {
        "configurable": {
            "source": "slack",
            "github_login": "mason-gh",
            "github_user_id": 4321,
            "slack_thread": {"triggering_user_name": "Mason"},
        }
    }
    identity = await resolve_triggering_user_identity(config)
    assert identity is not None
    assert identity.commit_name == "Mason"
    assert identity.commit_email == "4321+mason-gh@users.noreply.github.com"
    assert identity.github_login == "mason-gh"
    assert not identity.github_profile
    assert identity.display_name_source == "slack"
    assert identity.analytics_display_name == "Mason"


async def test_resolve_identity_without_github_login_is_none() -> None:
    config = {
        "configurable": {
            "source": "slack",
            "user_email": "mason@slack.example",
            "slack_thread": {
                "triggering_user_name": "Mason",
                "triggering_user_email": "mason@slack.example",
            },
        }
    }
    assert await resolve_triggering_user_identity(config) is None


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


async def test_public_profile_lookup_failure_is_nonfatal(
    github_client: _FakeAsyncClient, installation_token: None
) -> None:
    github_client._responses.append(_FakeResponse(404, {"message": "Not Found"}))
    assert await resolve_public_github_profile("mason-gh") is None

    ttl_cache.clear()
    github_client._responses.append(httpx2.ConnectError("boom"))
    assert await resolve_public_github_profile("mason-gh") is None


async def test_public_profile_lookup_skips_request_without_token(
    github_client: _FakeAsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_token(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(github_app, "get_github_app_installation_token", no_token)
    assert await resolve_public_github_profile("mason-gh") is None
    assert github_client.requests == []


async def test_public_profile_lookup_is_cached_per_login(
    github_client: _FakeAsyncClient, installation_token: None
) -> None:
    github_client._responses.append(
        _FakeResponse(200, {"id": 42, "login": "mason-gh", "name": "Mason"})
    )
    first = await resolve_public_github_profile("mason-gh")
    second = await resolve_public_github_profile("MASON-GH")
    assert first == second
    assert len(github_client.requests) == 1

    github_client._responses.append(_FakeResponse(404, {"message": "Not Found"}))
    assert await resolve_public_github_profile("other-user") is None
    assert len(github_client.requests) == 2


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


_USER_TOKEN = "user-oauth-token"
_GITHUB_USER = {"id": 7, "login": "mason-gh", "name": "Mason", "email": "m@example.com"}


async def test_second_worker_resolves_token_identity_without_calling_github(
    fake_store: FakeStore, github_client: _FakeAsyncClient
) -> None:
    github_client._responses.extend(
        [_FakeResponse(200, _GITHUB_USER), _FakeResponse(200, _GITHUB_USER)]
    )
    first = await resolve_triggering_user_identity({"configurable": {}}, _USER_TOKEN)

    ttl_cache.clear()  # a second worker starts with an empty in-process cache
    second = await resolve_triggering_user_identity({"configurable": {}}, _USER_TOKEN)

    assert first is not None
    assert first.github_login == "mason-gh"
    assert second == first
    assert [url for url, _headers in github_client.requests] == ["https://api.github.com/user"]


async def test_token_never_reaches_the_store_or_logs(
    fake_store: FakeStore,
    github_client: _FakeAsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    github_client._responses.extend(
        [_FakeResponse(200, _GITHUB_USER), _FakeResponse(200, _GITHUB_USER)]
    )
    await resolve_triggering_user_identity({"configurable": {}}, _USER_TOKEN)
    assert fake_store.items
    assert _USER_TOKEN not in repr(fake_store.items)

    async def unavailable(*_args: object) -> dict[str, object]:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(fake_store, "get_item", unavailable)
    ttl_cache.clear()
    with caplog.at_level(logging.DEBUG):
        assert await resolve_triggering_user_identity({"configurable": {}}, _USER_TOKEN)

    assert caplog.records
    assert all(_USER_TOKEN not in repr(vars(record)) for record in caplog.records)


@pytest.mark.parametrize(
    "miss",
    [
        _FakeResponse(401, {"message": "Bad credentials"}),
        httpx2.ConnectError("GitHub unreachable"),
    ],
)
async def test_token_lookup_without_identity_is_not_cached(
    fake_store: FakeStore,
    github_client: _FakeAsyncClient,
    installation_token: None,
    miss: _FakeResponse | Exception,
) -> None:
    github_client._responses.extend(
        [miss, _FakeResponse(404, {"message": "Not Found"}), _FakeResponse(200, _GITHUB_USER)]
    )
    config = {"configurable": {"github_login": "mason-gh", "github_user_id": 7}}

    fallback = await resolve_triggering_user_identity(config, _USER_TOKEN)
    assert fake_store.items == {}
    retried = await resolve_triggering_user_identity(config, _USER_TOKEN)

    assert fallback is not None
    assert not fallback.github_profile  # the config's identity
    assert retried is not None
    assert retried.github_profile  # GitHub was asked again, not a cached miss
