import json
from typing import Any

import httpx2
import pytest

import agent.utils.authorship as authorship
from agent.github import app as github_app
from agent.utils import ttl_cache
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    add_bot_coauthor_trailer,
    build_pr_attribution_footer,
    resolve_public_github_profile,
    resolve_triggering_user_identity,
)

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
