"""Tests for the Slack account-link prompt."""

from __future__ import annotations

from typing import TypedDict

import pytest

from agent.webhooks import common as webhook_common


class _Reply(TypedDict):
    channel_id: str
    thread_ts: str
    text: str


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")


def test_account_link_prompt_posts_generic_token_free_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt posts a plain settings link in the thread — no per-user token."""
    import asyncio

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    calls: dict[str, _Reply] = {}

    async def fake_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        calls["reply"] = {"channel_id": channel_id, "thread_ts": thread_ts, "text": text}
        return True

    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", fake_reply)

    asyncio.run(
        webhook_common._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="unlinked")
    )
    reply = calls["reply"]
    assert reply["channel_id"] == "C1"
    assert reply["thread_ts"] == "1.1"
    assert "https://app.example.com/my-settings" in reply["text"]
    # No signed account-link token may appear in the public thread.
    assert "link=" not in reply["text"]


def test_account_link_prompt_revoked_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    calls: dict[str, str] = {}

    async def fake_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        calls["text"] = text
        return True

    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", fake_reply)

    asyncio.run(
        webhook_common._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="revoked")
    )
    text = calls["text"]
    assert "no longer valid" in text
    assert "link=" not in text


def test_account_link_prompt_skips_when_dashboard_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    posted = False

    async def fake_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        nonlocal posted
        posted = True
        return True

    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", fake_reply)

    asyncio.run(
        webhook_common._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="unlinked")
    )
    assert posted is False
