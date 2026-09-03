"""Slack bot identity discovery from the bot token."""

from typing import Any

import pytest

from agent.utils import slack as slack_utils
from agent.webhooks import common


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _client(routes: dict[str, dict[str, Any]]) -> type:
    class Client:
        calls: list[str] = []
        headers: list[dict[str, str]] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> _Response:
            method = url.rsplit("/", 1)[1]
            type(self).calls.append(method)
            type(self).headers.append(dict(kwargs.get("headers") or {}))
            return _Response(routes.get(method, {"ok": False, "error": "unknown_method"}))

        async def get(self, url: str, **kwargs: Any) -> _Response:
            method = url.rsplit("/", 1)[1].split("?", 1)[0]
            type(self).calls.append(method)
            type(self).headers.append(dict(kwargs.get("headers") or {}))
            return _Response(routes.get(method, {"ok": False, "error": "unknown_method"}))

    return Client


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "")
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "")
    monkeypatch.setattr(common, "_SLACK_IDENTITY_ATTEMPTED_AT", None)
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")


async def test_fetch_identity_uses_auth_test_and_users_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        {
            "auth.test": {"ok": True, "user_id": "UBOT", "user": "bot", "team_id": "T1"},
            "users.info": {
                "ok": True,
                "user": {"name": "open-swe", "profile": {"display_name": "Open SWE"}},
            },
        }
    )
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)

    identity = await slack_utils.fetch_slack_bot_identity()

    assert identity == slack_utils.SlackBotIdentity(
        user_id="UBOT", username="open-swe", team_id="T1"
    )
    assert client.calls == ["auth.test", "users.info"]
    assert all(h.get("Authorization") == "Bearer xoxb-test" for h in client.headers)


async def test_fetch_identity_falls_back_to_auth_test_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        {"auth.test": {"ok": True, "user_id": "UBOT", "user": "openswe-bot", "team_id": "T1"}}
    )
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)

    identity = await slack_utils.fetch_slack_bot_identity()

    assert identity is not None
    assert identity.username == "openswe-bot"


async def test_fetch_identity_returns_none_on_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client({"auth.test": {"ok": False, "error": "invalid_auth"}})
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)

    assert await slack_utils.fetch_slack_bot_identity() is None


async def test_fetch_identity_returns_none_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> _Response:
            raise ConnectionError("no network")

    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", Client)

    assert await slack_utils.fetch_slack_bot_identity() is None


async def test_fetch_identity_without_token_makes_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")
    client = _client({})
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)

    assert await slack_utils.fetch_slack_bot_identity() is None
    assert client.calls == []


async def test_ensure_fills_empty_module_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake() -> slack_utils.SlackBotIdentity:
        return slack_utils.SlackBotIdentity("UBOT", "open-swe", "T1")

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)

    await common.ensure_slack_bot_identity()

    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UBOT", "open-swe")


async def test_ensure_respects_explicit_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "UENV")
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "env-name")
    called = False

    async def fake() -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)

    await common.ensure_slack_bot_identity()

    assert not called
    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UENV", "env-name")


async def test_ensure_skips_when_user_id_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "UENV")
    called = False

    async def fake() -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)

    await common.ensure_slack_bot_identity()

    assert not called
    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UENV", "")


async def test_ensure_keeps_explicit_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "custom-name")

    async def fake() -> slack_utils.SlackBotIdentity:
        return slack_utils.SlackBotIdentity("UBOT", "open-swe", "T1")

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)

    await common.ensure_slack_bot_identity()

    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UBOT", "custom-name")


async def test_ensure_throttles_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake() -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)

    await common.ensure_slack_bot_identity()
    await common.ensure_slack_bot_identity()

    assert calls == 1
