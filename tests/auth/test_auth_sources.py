import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from openswe.github import token as auth


def _slack_config(github_login: str | None = "mason-gh") -> dict:
    configurable: dict = {
        "source": "slack",
        "user_email": "mason@example.com",
        "thread_id": "t1",
    }
    if github_login is not None:
        configurable["github_login"] = github_login
    return {"configurable": configurable}


def _stub_dashboard_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None,
    expires_at: str | None = "2099-01-01T00:00:00Z",
    cached: tuple[str | None, str | None] = (None, None),
) -> None:
    from openswe.dashboard import profiles
    from openswe.github.thread_token import cache_github_token_for_thread

    if cached[0]:
        cache_github_token_for_thread("t1", cached[0], cached[1], principal="login:mason-gh")

    async def fake_get_valid(login: str):
        return token

    async def fake_get_record(login: str):
        return {"token_expires_at": expires_at}

    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(
                    return_value={"metadata": {"visibility": "private", "owner_login": "mason-gh"}}
                )
            )
        ),
    )
    monkeypatch.setattr(profiles, "get_valid_access_token", fake_get_valid)
    monkeypatch.setattr(profiles, "get_oauth_token_record", fake_get_record)


def test_resolve_github_token_slack_uses_dashboard_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token="user-tok")
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, expires_at = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))

    assert token == "user-tok"
    assert expires_at == "2099-01-01T00:00:00Z"


def test_resolve_github_token_slack_ignores_stale_thread_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A private run must refresh its owner token instead of trusting an older cache entry.
    _stub_dashboard_store(
        monkeypatch,
        token="bob-token",
        cached=("alice-token", "2099-01-01T00:00:00Z"),
    )
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, _ = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))

    assert token == "bob-token"


def test_resolve_github_token_slack_no_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    with pytest.raises(auth.GitHubUserAuthRequired):
        asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))


def test_resolve_github_token_per_user_wins_over_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token="user-tok")
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fail_bot(thread_id: str):
        raise AssertionError("bot token must not be used when a user token exists")

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fail_bot)

    token, _ = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))
    assert token == "user-tok"


def test_private_slack_no_token_requires_auth_in_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    with pytest.raises(auth.GitHubUserAuthRequired):
        asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))


def _linear_config(github_login: str | None = "mason-gh") -> dict:
    configurable: dict = {
        "source": "linear",
        "user_email": "mason@example.com",
        "thread_id": "t1",
    }
    if github_login is not None:
        configurable["github_login"] = github_login
    return {"configurable": configurable}


def test_resolve_github_token_linear_uses_dashboard_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token="user-tok")
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, expires_at = asyncio.run(auth.resolve_github_token(_linear_config(), "t1"))

    assert token == "user-tok"
    assert expires_at == "2099-01-01T00:00:00Z"


def test_resolve_github_token_linear_no_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    with pytest.raises(auth.GitHubUserAuthRequired):
        asyncio.run(auth.resolve_github_token(_linear_config(), "t1"))


def test_private_linear_no_token_requires_auth_in_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    with pytest.raises(auth.GitHubUserAuthRequired):
        asyncio.run(auth.resolve_github_token(_linear_config(), "t1"))


@pytest.mark.parametrize("source", ["github"])
def test_resolve_github_token_bot_only_mode_non_slack_uses_bot(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": {}}))
        ),
    )
    config = {"configurable": {"source": source, "github_login": "octo", "thread_id": "t1"}}
    token, _ = asyncio.run(auth.resolve_github_token(config, "t1"))
    assert token == "bot-tok"
