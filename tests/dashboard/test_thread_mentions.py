from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.database import postgres
from openswe.notifications import service
from openswe.notifications.service import AppNotification
from openswe.threads import index, mentions
from openswe.users import User


@pytest.mark.parametrize("review", [False, True])
async def test_mentions_are_deduplicated_and_do_not_disclose_private_content(
    monkeypatch: pytest.MonkeyPatch, registry_db: None, review: bool
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    alice = await User.sign_in("github", "1", login="alice")
    await alice.link("slack", "U123SELF")
    bob = await User.sign_in("github", "2", login="bob")
    await bob.link("slack", "U123BOB")
    metadata = {"source": "dashboard", "visibility": "private"}
    if review:
        metadata.update(source="review_chat", repo_owner="acme", repo_name="demo", pr_number=1)
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata=metadata)
    notification = mentions.ThreadMentions(
        thread_id, "alice", "message-1", "secret <@U123SELF> <@U123BOB> <@U123BOB>"
    )
    await notification.notify()
    await notification.notify()
    pending = await AppNotification.pending(bob.id)
    assert len(pending) == 1
    assert "secret" not in pending[0].body
    assert ("/agents/reviews/acme/demo/1" if review else f"/agents/{thread_id}") in pending[0].url
    assert await AppNotification.pending(alice.id) == []
    assert not await AppNotification.acknowledge(alice.id, pending[0].id)
    assert await AppNotification.acknowledge(bob.id, pending[0].id)
    async with postgres.transaction() as conn:
        await conn.execute(text("UPDATE app_notification SET fallback_at = clock_timestamp()"))
    dm = AsyncMock()
    monkeypatch.setattr(service, "post_dm", dm)
    await AppNotification.fallback()
    dm.assert_not_awaited()


async def test_unacknowledged_notification_retries_failed_fallback(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "bob")
    bob = await User.sign_in("github", "2", login="bob")
    await bob.link("slack", "U123BOB")
    await AppNotification.send(
        bob,
        key="message-1",
        title="Mention",
        body="Open thread",
        url="/agents",
        fallback_text="Fallback",
    )
    async with postgres.transaction() as conn:
        await conn.execute(text("UPDATE app_notification SET fallback_at = clock_timestamp()"))
    dm = AsyncMock(side_effect=[RuntimeError("offline"), ("D123", "1.0")])
    monkeypatch.setattr(service, "post_dm", dm)
    await AppNotification.fallback()
    assert len(await AppNotification.pending(bob.id)) == 1
    await AppNotification.fallback()
    await AppNotification.fallback()
    assert dm.await_count == 2
    assert await AppNotification.pending(bob.id) == []
