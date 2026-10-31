from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.threads import mentions


@pytest.mark.parametrize("review", [False, True])
async def test_mentions_are_deduplicated_and_do_not_disclose_private_content(
    monkeypatch: pytest.MonkeyPatch, registry_db: None, review: bool
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(
        mentions.User,
        "for_login",
        AsyncMock(return_value=SimpleNamespace(slack_user_id="U123SELF")),
    )
    metadata = {"source": "dashboard", "visibility": "private"}
    if review:
        metadata.update(source="review_chat", repo_owner="acme", repo_name="demo", pr_number=1)
    monkeypatch.setattr(
        mentions.THREADS, "get", AsyncMock(return_value=SimpleNamespace(metadata=metadata))
    )
    dm = AsyncMock(return_value=True)
    monkeypatch.setattr(mentions, "send_dm", dm)
    notification = mentions.ThreadMentions(
        "tid", "alice", "message-1", "secret <@U123SELF> <@U123BOB> <@U123BOB>"
    )
    await notification.notify()
    await notification.notify()
    dm.assert_awaited_once()
    recipient, text = dm.call_args.args
    assert recipient == "U123BOB"
    assert "secret" not in text
    assert "sign in with an account that has access" in text
    assert ("/agents/reviews/acme/demo/1" if review else "/agents/tid") in text


async def test_failed_dm_can_be_retried(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(mentions.User, "for_login", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mentions.THREADS, "get", AsyncMock(return_value=SimpleNamespace(metadata={}))
    )
    dm = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(mentions, "send_dm", dm)
    notification = mentions.ThreadMentions("tid", "alice", "message-1", "<@U123BOB>")
    await notification.notify()
    await notification.notify()
    assert dm.await_count == 2
