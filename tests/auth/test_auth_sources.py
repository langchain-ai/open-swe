from __future__ import annotations

import asyncio

import pytest

from agent.utils import auth, github_token


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    github_token._GITHUB_TOKEN_CACHE.clear()


@pytest.mark.parametrize("source", ["linear", "slack", "github", "dashboard", "schedule"])
def test_resolve_github_token_uses_installation_token_for_every_source(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    calls: list[str | None] = []

    async def fake_app_token(*, target_repo: str | None = None) -> tuple[str, str]:
        calls.append(target_repo)
        return "installation-token", "2099-01-01T00:00:00Z"

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)
    config = {
        "configurable": {
            "source": source,
            "github_login": "octocat",
            "user_email": "octocat@example.com",
            "repo": {"owner": "acme", "name": "widgets"},
        }
    }

    token, expires_at = asyncio.run(auth.resolve_github_token(config, "thread-1"))

    assert (token, expires_at) == ("installation-token", "2099-01-01T00:00:00Z")
    assert calls == ["acme/widgets"]
    assert (
        github_token.get_github_token(
            {"configurable": {"thread_id": "thread-1", "github_login": "someone-else"}}
        )
        == "installation-token"
    )


def test_resolve_github_token_ignores_cached_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    github_token.cache_github_token_for_thread(
        "thread-1",
        "cached-user-token",
        principal=github_token.github_token_principal(login="octocat"),
    )

    async def fake_app_token(*, target_repo: str | None = None) -> tuple[str, None]:
        assert target_repo == "acme/widgets"
        return "installation-token", None

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)
    config = {
        "configurable": {
            "source": "slack",
            "github_login": "octocat",
            "repo": {"owner": "acme", "name": "widgets"},
        }
    }

    token, _ = asyncio.run(auth.resolve_github_token(config, "thread-1"))

    assert token == "installation-token"
    assert (
        github_token.get_github_token(
            config | {"configurable": {**config["configurable"], "thread_id": "thread-1"}}
        )
        == "installation-token"
    )


def test_resolve_github_token_without_requester_mapping_uses_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_app_token(*, target_repo: str | None = None) -> tuple[str, None]:
        assert target_repo is None
        return "installation-token", None

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)

    token, _ = asyncio.run(
        auth.resolve_github_token({"configurable": {"source": "slack"}}, "thread-1")
    )

    assert token == "installation-token"


def test_resolve_github_token_fails_closed_when_installation_token_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_app_token(*, target_repo: str | None = None) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)

    with pytest.raises(RuntimeError, match="GitHub App installation token unavailable"):
        asyncio.run(
            auth.resolve_github_token(
                {
                    "configurable": {
                        "source": "dashboard",
                        "github_login": "octocat",
                        "repo": {"owner": "acme", "name": "widgets"},
                    }
                },
                "thread-1",
            )
        )


def test_resolve_github_token_requires_configurable_state() -> None:
    with pytest.raises(RuntimeError, match="missing configurable state"):
        asyncio.run(auth.resolve_github_token({}, "thread-1"))


def test_resolve_github_token_requires_source() -> None:
    with pytest.raises(RuntimeError, match="missing source"):
        asyncio.run(auth.resolve_github_token({"configurable": {}}, "thread-1"))
