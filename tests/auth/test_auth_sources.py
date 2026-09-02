import asyncio
import logging
from typing import Any

import pytest

from agent.auth import resolve as auth


def test_leave_failure_comment_posts_generic_token_free_slack_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack auth failures post a generic notice, never the (possibly sensitive) message."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    thread_called: dict[str, str] = {}

    async def fake_post_slack_thread_reply(
        channel_id: str, thread_ts: str, message: str, **kwargs: Any
    ) -> bool:
        thread_called["channel_id"] = channel_id
        thread_called["thread_ts"] = thread_ts
        thread_called["message"] = message
        return True

    monkeypatch.setattr(auth, "post_slack_thread_reply", fake_post_slack_thread_reply)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {
            "configurable": {
                "slack_thread": {
                    "channel_id": "C123",
                    "thread_ts": "1.2",
                    "triggering_user_id": "U123",
                }
            }
        },
    )

    # Pass a message that embeds a per-user auth URL; it must NOT be echoed publicly.
    asyncio.run(auth.leave_failure_comment("slack", "Click https://auth.example/secret-token"))

    assert thread_called["channel_id"] == "C123"
    assert thread_called["thread_ts"] == "1.2"
    assert "secret-token" not in thread_called["message"]
    assert "https://app.example.com/my-settings" in thread_called["message"]


def test_resolve_token_from_email_logs_legacy_only_user_in_background(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "t1",
                "github_login": "mason-gh",
            }
        },
    )

    async def fake_user_info(email: str) -> dict[str, str]:
        return {"ls_user_id": "user-1", "tenant_id": "tenant-1"}

    async def fake_legacy_token(user_id: str, tenant_id: str) -> dict[str, str]:
        return {"token": "legacy-token"}

    from agent.dashboard import profiles

    monkeypatch.setattr(auth, "get_ls_user_id_from_email", fake_user_info)
    monkeypatch.setattr(auth, "get_github_token_for_user", fake_legacy_token)

    async def scenario() -> str:
        lookup_started = asyncio.Event()
        release_lookup = asyncio.Event()

        async def fake_open_swe_token(login: str) -> None:
            lookup_started.set()
            await release_lookup.wait()

        monkeypatch.setattr(profiles, "get_valid_access_token", fake_open_swe_token)
        token, _ = await auth.resolve_token_from_email("mason@example.com", "github")
        await lookup_started.wait()
        assert "legacy_github_auth_migration_impact " not in caplog.text
        tasks = list(auth._legacy_auth_impact_tasks)
        release_lookup.set()
        await asyncio.gather(*tasks)
        return token

    with caplog.at_level(logging.INFO, logger=auth.logger.name):
        token = asyncio.run(scenario())

    assert token == "legacy-token"
    assert (
        "legacy_github_auth_migration_impact source=github github_login=mason-gh "
        "requires_reauth=True" in caplog.text
    )
    assert "legacy-token" not in caplog.text


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
    from agent.dashboard import profiles

    async def fake_get_from_thread(thread_id: str):
        return cached

    async def fake_get_valid(login: str):
        return token

    async def fake_get_record(login: str):
        return {"token_expires_at": expires_at}

    monkeypatch.setattr(auth, "get_github_token_from_thread", fake_get_from_thread)
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
    # Slack thread ids are shared, so a prior user's cached token must NOT be
    # returned. Resolution always goes by github_login via the dashboard store.
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


def test_resolve_github_token_slack_no_token_falls_back_to_bot_in_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    token, expires_at = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))
    assert (token, expires_at) == ("bot-tok", None)


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


def test_resolve_github_token_linear_no_token_falls_back_to_bot_in_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    token, expires_at = asyncio.run(auth.resolve_github_token(_linear_config(), "t1"))
    assert (token, expires_at) == ("bot-tok", None)


@pytest.mark.parametrize("source", ["github"])
def test_resolve_github_token_bot_only_mode_non_slack_uses_bot(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    config = {"configurable": {"source": source, "github_login": "octo", "thread_id": "t1"}}
    token, _ = asyncio.run(auth.resolve_github_token(config, "t1"))
    assert token == "bot-tok"
