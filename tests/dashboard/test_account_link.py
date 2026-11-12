"""Tests for the Slack account-link prompt."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openswe.webhooks import common as webhook_common


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")


@pytest.mark.parametrize("thread_ts", ["1.1", "0", ""])
@pytest.mark.parametrize("langsmith", [False, True])
@pytest.mark.parametrize("reason", ["unlinked", "revoked"])
@pytest.mark.parametrize("delivered", [False, True, RuntimeError("Slack unavailable")])
async def test_account_link_prompt_is_ephemeral_only(
    monkeypatch: pytest.MonkeyPatch,
    langsmith: bool,
    thread_ts: str,
    reason: webhook_common.AccountLinkReason,
    delivered: bool | RuntimeError,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    ephemeral = AsyncMock(
        side_effect=delivered if isinstance(delivered, RuntimeError) else None,
        return_value=delivered,
    )
    public = AsyncMock(side_effect=AssertionError("Account prompts must not be public"))
    monkeypatch.setattr(webhook_common, "post_slack_ephemeral_message", ephemeral)
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", public)

    await webhook_common.post_account_link_prompt(
        "C1", thread_ts, "U1", "d@x.com", reason=reason, langsmith=langsmith
    )

    ephemeral.assert_awaited_once()
    channel, recipient, text = ephemeral.await_args.args
    assert (channel, recipient) == ("C1", "U1")
    assert ephemeral.await_args.kwargs == {"thread_ts": "" if thread_ts == "0" else thread_ts}
    url = (
        webhook_common.LangSmithLogin.slack_onboarding_url()
        if langsmith
        else "https://app.example.com/my-settings/connections"
    )
    assert url in text
    assert "link=" not in text
    public.assert_not_awaited()


def test_account_link_prompt_skips_when_dashboard_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    ephemeral = AsyncMock()
    monkeypatch.setattr(webhook_common, "post_slack_ephemeral_message", ephemeral)

    asyncio.run(
        webhook_common.post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="unlinked")
    )
    ephemeral.assert_not_awaited()
