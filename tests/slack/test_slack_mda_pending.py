import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from openswe.slack import mda_forwarding
from openswe.slack.request import SlackRequest
from openswe.threads import index, router

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "0b9e2d6c-6a3b-5f0e-9a52-2f3d1c4b5a69"


def _request(text: str) -> SlackRequest:
    return SlackRequest(channel_id="C1", thread_ts="1.0", text=text, thread_id=THREAD)


async def test_a_message_remembered_while_another_is_recalled_keeps_both() -> None:
    await index.create(THREAD, runtime="mda", metadata={"title": "kept"})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled, *_ = await asyncio.gather(
        mda_forwarding.recall(THREAD, "1.1"),
        *(mda_forwarding.remember(_request(f"B{n}"), None, f"2.{n}") for n in range(10)),
    )

    assert recalled is not None and recalled[0].text == "A"
    for n in range(10):
        later = await mda_forwarding.recall(THREAD, f"2.{n}")
        assert later is not None and later[0].text == f"B{n}"
    thread = await index.get(THREAD)
    assert thread is not None and thread.metadata["title"] == "kept"


async def test_a_message_is_recalled_once() -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled = await asyncio.gather(*(mda_forwarding.recall(THREAD, "1.1") for _ in range(5)))

    assert [r[0].text for r in recalled if r is not None] == ["A"]


async def test_expired_requests_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("old"), None, "1.1")
    monkeypatch.setattr(time, "time", lambda: 1e12)

    await mda_forwarding.remember(_request("new"), None, "2.1")

    thread = await index.get(THREAD)
    assert thread is not None
    assert list(thread.metadata[mda_forwarding.PENDING_KEY]) == ["2.1"]
    assert await mda_forwarding.recall(THREAD, "1.1") is None


@pytest.fixture
def replies(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The error reply the Slack thread gets, as a stand-in."""
    notify = AsyncMock()
    monkeypatch.setattr(mda_forwarding, "notify_slack_processing_error", notify)
    return notify


async def test_a_lost_message_is_reported_once_and_never_runs(replies: AsyncMock) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    reported = await asyncio.gather(
        *(mda_forwarding.report_lost(THREAD, "1.1", "lost") for _ in range(3))
    )

    assert reported.count(True) == 1
    [(request, repo, error)] = [call.args for call in replies.await_args_list]
    assert (request.text, repo) == ("A", None)
    assert isinstance(error, mda_forwarding.MdaHandoffLost)
    # The channel asking afterwards starts nothing: the person already heard.
    assert await mda_forwarding.recall(THREAD, "1.1") is None


async def test_a_message_the_channel_took_is_not_reported(replies: AsyncMock) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")
    assert await mda_forwarding.recall(THREAD, "1.1") is not None

    assert await mda_forwarding.report_lost(THREAD, "1.1", "lost") is False
    replies.assert_not_awaited()


async def test_the_sweep_reports_messages_the_channel_never_asked_about(
    monkeypatch: pytest.MonkeyPatch, replies: AsyncMock
) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 120)
    await mda_forwarding.remember(_request("lost"), None, "1.1")
    monkeypatch.setattr(time, "time", lambda: now)
    await mda_forwarding.remember(_request("fresh"), None, "2.1")

    assert await mda_forwarding.sweep_lost_requests() == 1
    assert await mda_forwarding.sweep_lost_requests() == 0

    [(request, _repo, _error)] = [call.args for call in replies.await_args_list]
    assert request.text == "lost"
    thread = await index.get(THREAD)
    assert thread is not None
    assert list(thread.metadata[mda_forwarding.PENDING_KEY]) == ["2.1"]


async def test_a_claimed_conversation_is_private_until_its_run_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = AsyncMock()
    # The Agent Server's own copy of the thread, as a stand-in.
    monkeypatch.setattr(type(router.RUNTIMES), "local", property(lambda _self: local))
    thread_id = await mda_forwarding.claim_conversation("A1", "T1", "C9", "9.1")

    assert thread_id == mda_forwarding.mda_thread_id("A1", "T1", "C9", "9.1")
    thread = await index.get(thread_id)
    assert thread is not None
    assert thread.metadata["visibility"] == "private"
    assert thread.metadata["source"] == "slack"
    assert thread.metadata["channel"] == "slack"
    local.create.assert_awaited_once_with(thread_id, thread.metadata)
    # Another app's conversation can't take the Slack thread now.
    assert await mda_forwarding.claim_conversation("A2", "T1", "C9", "9.1") is None
