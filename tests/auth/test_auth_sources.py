from __future__ import annotations

import asyncio
import os

import pytest

from agent.utils import auth, github_app, github_token


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    github_token._GITHUB_TOKEN_CACHE.clear()


@pytest.mark.parametrize("source", ["linear", "slack", "github", "dashboard", "schedule"])
def test_resolve_github_token_uses_scoped_installation_token_for_every_source(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    calls: list[tuple[str | None, list[str] | None]] = []

    async def fake_app_token(
        *, target_repo: str | None = None, repositories: list[str] | None = None
    ) -> tuple[str, str]:
        calls.append((target_repo, repositories))
        return "installation-token", "2099-01-01T00:00:00Z"

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)
    config = {
        "configurable": {
            "source": source,
            "github_login": "octocat",
            "repo": {"owner": "acme", "name": "widgets"},
        }
    }

    token, expires_at = asyncio.run(auth.resolve_github_token(config, "thread-1"))

    assert (token, expires_at) == ("installation-token", "2099-01-01T00:00:00Z")
    assert calls == [("acme/widgets", ["widgets"])]
    assert (
        github_token.get_github_token(
            {"configurable": {"thread_id": "thread-1", "github_login": "someone-else"}}
        )
        == "installation-token"
    )


def test_resolve_github_token_uses_team_default_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_team_default_repo",
        lambda: _coro({"owner": "default-owner", "name": "default-repo"}),
    )

    async def fake_app_token(**kwargs: object) -> tuple[str, None]:
        assert kwargs == {
            "target_repo": "default-owner/default-repo",
            "repositories": ["default-repo"],
        }
        return "installation-token", None

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)

    token, _ = asyncio.run(
        auth.resolve_github_token({"configurable": {"source": "slack"}}, "thread-1")
    )

    assert token == "installation-token"


def test_resolve_github_token_ignores_cached_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    github_token.cache_github_token_for_thread(
        "thread-1",
        "cached-user-token",
        principal=github_token.github_token_principal(login="octocat"),
    )

    async def fake_app_token(**kwargs: object) -> tuple[str, None]:
        assert kwargs == {"target_repo": "acme/widgets", "repositories": ["widgets"]}
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


def test_resolve_github_token_fails_closed_without_trusted_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_team_default_repo", lambda: _coro(None))

    async def fail_mint(**_kwargs: object) -> tuple[None, None]:
        raise AssertionError("missing repository context must not mint a token")

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fail_mint)

    with pytest.raises(RuntimeError, match="missing trusted repository context"):
        asyncio.run(auth.resolve_github_token({"configurable": {"source": "slack"}}, "thread-1"))


def test_repo_less_schedule_token_mint_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_team_default_repo", lambda: _coro(None))

    async def fail_mint(**_kwargs: object) -> tuple[None, None]:
        raise AssertionError("repo-less schedules must not mint an installation-wide token")

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fail_mint)

    with pytest.raises(RuntimeError, match="missing trusted repository context"):
        asyncio.run(
            auth.resolve_github_token(
                {
                    "configurable": {
                        "source": "schedule",
                        "schedule_id": "sched_repo_less",
                    }
                },
                "thread-schedule",
            )
        )


def test_missing_app_credentials_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            auth.resolve_github_token(
                {
                    "configurable": {
                        "source": "dashboard",
                        "repo": {"owner": "acme", "name": "widgets"},
                    }
                },
                "thread-1",
            )
        )

    message = str(exc_info.value)
    assert "GitHub App credentials are missing" in message
    assert "GITHUB_APP_ID" in message
    assert "GITHUB_APP_PRIVATE_KEY" in message
    assert "No GitHub App installation covers repository" not in message


def test_transferred_repo_error_with_app_id_and_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingInstallationResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {}

    class MissingInstallationClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> MissingInstallationClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, **_kwargs: object) -> MissingInstallationResponse:
            assert url == "https://api.github.com/repos/mobilyze-llc/open-swe/installation"
            return MissingInstallationResponse()

        async def post(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing installation must not mint a token")

    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", os.environ["GITHUB_APP_ID"])
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", os.environ["GITHUB_APP_PRIVATE_KEY"])
    monkeypatch.setattr(
        github_app, "GITHUB_APP_INSTALLATION_ID", os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
    )
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    monkeypatch.setattr(github_app.httpx, "AsyncClient", MissingInstallationClient)
    monkeypatch.delenv(github_app.GITHUB_APP_TARGET_REPO_ENV, raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            auth.resolve_github_token(
                {
                    "configurable": {
                        "source": "dashboard",
                        "repo": {"owner": "mobilyze-llc", "name": "open-swe"},
                    }
                },
                "thread-1",
            )
        )

    message = str(exc_info.value)
    assert "mobilyze-llc/open-swe" in message
    assert "No GitHub App installation covers repository" in message
    assert "canonical owner" in message
    assert "GITHUB_APP_INSTALLATION_ID" not in message


def test_resolve_github_token_requires_configurable_state() -> None:
    with pytest.raises(RuntimeError, match="missing configurable state"):
        asyncio.run(auth.resolve_github_token({}, "thread-1"))


def test_resolve_github_token_requires_source() -> None:
    with pytest.raises(RuntimeError, match="missing source"):
        asyncio.run(auth.resolve_github_token({"configurable": {}}, "thread-1"))


async def _coro(value: object) -> object:
    return value


def test_refresh_github_token_mints_after_the_cached_token_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_token.cache_github_token_for_thread(
        "thread-1", "expired-token", expires_at="2000-01-01T00:00:00Z", is_bot_token=True
    )
    config = {
        "configurable": {
            "source": "github",
            "thread_id": "thread-1",
            "repo": {"owner": "acme", "name": "widgets"},
        }
    }
    assert github_token.get_github_token(config) is None

    async def fake_app_token(**kwargs: object) -> tuple[str, str]:
        assert kwargs == {"target_repo": "acme/widgets", "repositories": ["widgets"]}
        return "fresh-token", "2099-01-01T00:00:00Z"

    monkeypatch.setattr(auth, "get_github_app_installation_token_with_expiry", fake_app_token)

    assert asyncio.run(auth.refresh_github_token(config, "thread-1")) == "fresh-token"
    assert github_token.get_github_token(config) == "fresh-token"


def test_refresh_github_token_returns_none_when_it_cannot_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_team_default_repo", lambda: _coro(None))

    assert (
        asyncio.run(auth.refresh_github_token({"configurable": {"source": "github"}}, "t")) is None
    )
