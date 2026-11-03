import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from openswe.jobs import queue
from openswe.notifications import service
from openswe.notifications.service import AppNotification
from openswe.slack.user_options import SlackUserOption
from openswe.threads import index, mentions
from openswe.users import User


async def test_titles_show_mentions_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    people = [SlackUserOption(id="W123BOB", name="bob", display_name="Bob", real_name="Bob B")]
    monkeypatch.setattr(SlackUserOption, "all", AsyncMock(return_value=people))
    assert (
        await SlackUserOption.readable("Ask <@W123BOB> and <@U999> in <#C123|eng>")
        == "Ask @Bob and @someone in #eng"
    )
    monkeypatch.setattr(SlackUserOption, "all", AsyncMock(side_effect=RuntimeError("down")))
    assert await SlackUserOption.readable("Ask <@W123BOB>") == "Ask @someone"


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
    dm = AsyncMock()
    monkeypatch.setattr(service, "post_dm", dm)
    await AppNotification.deliver_fallback(bob.id, f"mention:{thread_id}:message-1")
    dm.assert_not_awaited()


async def test_mentions_cap_and_rate_limit_sender(monkeypatch, registry_db) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    await User.sign_in("github", "1", login="alice")
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata={"source": "dashboard"})
    send = AsyncMock()
    monkeypatch.setattr(AppNotification, "send_to_slack", send)
    tokens = " ".join(f"<@U123{i:03}>" for i in range(100))
    for number in range(5):
        await mentions.ThreadMentions(thread_id, "alice", str(number), tokens).notify()
        assert send.await_count <= min(10 * (number + 1), 30)
    assert send.await_count == 30


async def test_one_failed_recipient_does_not_stop_other_mentions(monkeypatch, registry_db) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    await User.sign_in("github", "1", login="alice")
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata={"source": "dashboard"})
    send = AsyncMock(side_effect=[RuntimeError("offline"), None])
    monkeypatch.setattr(AppNotification, "send_to_slack", send)
    await mentions.ThreadMentions(thread_id, "alice", "m1", "<@U123BAD> <@U123GOOD>").notify()
    assert send.await_count == 2


async def test_dispatch_does_not_wait_for_notification_delivery(monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class HeldMentions(mentions.ThreadMentions):
        async def notify(self) -> None:
            entered.set()
            await release.wait()

    notification = HeldMentions("tid", "alice", "m1", "<@U123BOB>")
    notification.dispatch()
    await entered.wait()
    assert notification._pending
    release.set()
    await asyncio.gather(*notification._pending)


async def test_recent_desktop_users_get_a_longer_slack_fallback_delay(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    alice = await User.sign_in("github", "1", login="alice")
    bob = await User.sign_in("github", "2", login="bob")
    await bob.note_desktop_use()
    for user in (alice, await User.get(bob.id)):
        assert user is not None
        await AppNotification.send(
            user, key="m1", title="Mention", body="", url="/agents", fallback_text="Mention"
        )
    delays = {}
    for user in (alice, bob):
        job = await queue.get(f"{service.JOB_KIND}:{user.id}:m1")
        assert job is not None
        delays[user.login_for("github")] = job.due_at - job.created_at
    assert delays["alice"] < timedelta(minutes=2)
    assert timedelta(hours=7, minutes=59) < delays["bob"] <= timedelta(hours=8, seconds=5)
