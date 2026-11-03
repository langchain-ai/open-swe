import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from openswe.jobs import queue
from openswe.notifications import service
from openswe.notifications.service import AppNotification
from openswe.threads import index, mentions
from openswe.threads.mention_directory import MentionDirectory
from openswe.users import User
from openswe.users.mentions import PersonMention


def test_mentions_carry_the_name_they_showed() -> None:
    bob = "<@user:01900000-0000-7000-8000-000000000002|Bob B>"
    assert PersonMention.readable(f"Ask {bob} in <#C123|eng>") == "Ask @Bob B in #eng"
    assert PersonMention.leads(f"  {bob} take a look")
    assert not PersonMention.leads(f"take a look {bob}")
    assert not PersonMention.leads("<@U123BOB> take a look")


@pytest.mark.parametrize("review", [False, True])
async def test_mentions_are_deduplicated_and_do_not_disclose_private_content(
    monkeypatch: pytest.MonkeyPatch, registry_db: None, review: bool
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    alice = await User.sign_in("github", "1", login="alice")
    bob = await User.sign_in("github", "2", login="bob")
    metadata = {"source": "dashboard", "visibility": "private"}
    if review:
        metadata.update(source="review_chat", repo_owner="acme", repo_name="demo", pr_number=1)
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata=metadata)
    notification = mentions.ThreadMentions(
        thread_id,
        "alice",
        "message-1",
        f"secret <@user:{alice.id}|Alice> <@user:{bob.id}|Bob> <@user:{bob.id}|Bob>",
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
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", ",".join(["alice", *(f"p{i}" for i in range(100))]))
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    await User.sign_in("github", "1", login="alice")
    people = [await User.sign_in("github", str(100 + i), login=f"p{i}") for i in range(100)]
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata={"source": "dashboard"})
    send = AsyncMock()
    monkeypatch.setattr(AppNotification, "send", send)
    tokens = " ".join(f"<@user:{person.id}|{i}>" for i, person in enumerate(people))
    for number in range(5):
        await mentions.ThreadMentions(thread_id, "alice", str(number), tokens).notify()
        assert send.await_count <= min(10 * (number + 1), 30)
    assert send.await_count == 30


async def test_one_failed_recipient_does_not_stop_other_mentions(monkeypatch, registry_db) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bad,good")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    await User.sign_in("github", "1", login="alice")
    bad = await User.sign_in("github", "2", login="bad")
    good = await User.sign_in("github", "3", login="good")
    thread_id = str(uuid7())
    await index.create(thread_id, runtime="mda", metadata={"source": "dashboard"})
    send = AsyncMock(side_effect=[RuntimeError("offline"), None])
    monkeypatch.setattr(AppNotification, "send", send)
    text = f"<@user:{bad.id}|Bad> <@user:{good.id}|Good>"
    await mentions.ThreadMentions(thread_id, "alice", "m1", text).notify()
    assert send.await_count == 2


async def test_dispatch_does_not_wait_for_notification_delivery(monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class HeldMentions(mentions.ThreadMentions):
        async def notify(self) -> None:
            entered.set()
            await release.wait()

    notification = HeldMentions("tid", "alice", "m1", f"<@user:{uuid7()}|Bob>")
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


async def test_directory_offers_people_in_the_conversation_first(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "viewer,ada,zed")
    viewer = await User.sign_in("github", "1", login="viewer")
    await User.sign_in("github", "2", login="ada")
    zed = await User.sign_in("github", "3", login="zed")
    metadata = {f"participant_identity:user:{zed.id}": f"user:{zed.id}"}
    directory = await MentionDirectory.for_conversation(viewer.id, str(uuid7()), metadata)
    assert [(person.login, person.involved) for person in directory.people] == [
        ("zed", True),
        ("ada", False),
    ]


async def test_tools_resolve_a_person_from_any_reference(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "bob")
    bob = await User.sign_in("github", "2", login="bob")
    await bob.link("slack", "U123BOB")
    for reference in (
        f"user:{bob.id}",
        str(bob.id),
        f"<@user:{bob.id}|Bob>",
        "U123BOB",
        "<@U123BOB>",
        "@bob",
    ):
        person = await User.resolve(reference)
        assert person is not None and person.id == bob.id, reference
    assert await User.resolve("nobody") is None
