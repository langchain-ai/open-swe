from __future__ import annotations

import asyncio

import pytest

from agent.utils import auth


def test_leave_failure_comment_posts_generic_token_free_slack_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack auth failures post a generic notice, never the (possibly sensitive) message."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    thread_called: dict[str, str] = {}

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, message: str) -> bool:
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

    async def fake_get_value(namespace, key):
        return {"token_expires_at": expires_at}

    monkeypatch.setattr(auth, "get_github_token_from_thread", fake_get_from_thread)
    monkeypatch.setattr(profiles, "get_valid_access_token", fake_get_valid)
    monkeypatch.setattr(profiles, "_get_value", fake_get_value)


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


def test_resolve_github_token_delivery_queue_uses_provider_pat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Resolved:
        token = "ghp-delivery-token"

    async def fake_resolve_provider_pat(
        login: str,
        *,
        provider: str,
        project_id: str = "",
        action: str = "provider_access",
    ):
        assert login == "octocat"
        assert provider == "github"
        assert action == "delivery_queue_run"
        return Resolved()

    monkeypatch.setattr(
        "agent.dashboard.provider_pat_vault.resolve_provider_pat", fake_resolve_provider_pat
    )

    config = {
        "configurable": {
            "source": "delivery_queue",
            "thread_id": "t1",
            "delivery_worker_input": {
                "credential_policy": {
                    "provider": "github",
                    "identity": "github:user:octocat",
                    "requires_user_pat": True,
                }
            },
        }
    }

    token, expires_at = asyncio.run(auth.resolve_github_token(config, "t1"))

    assert (token, expires_at) == ("ghp-delivery-token", None)


def test_resolve_github_token_delivery_queue_requires_identity() -> None:
    config = {
        "configurable": {
            "source": "delivery_queue",
            "thread_id": "t1",
            "delivery_worker_input": {"credential_policy": {"provider": "github"}},
        }
    }

    with pytest.raises(RuntimeError, match="missing credential identity"):
        asyncio.run(auth.resolve_github_token(config, "t1"))


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


@pytest.mark.parametrize("source", ["github", "linear"])
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
