import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.slack import mda_forwarding
from openswe.slack.records import SlackThreadMappingError
from openswe.slack.request import SlackRequest
from openswe.threads import index, router
from openswe.webhooks.common import ThreadRecord, visibility_is_settled

pytestmark = [pytest.mark.usefixtures("registry_db"), pytest.mark.mda_lookups]

THREAD = "0b9e2d6c-6a3b-5f0e-9a52-2f3d1c4b5a69"


def _request(text: str) -> SlackRequest:
    return SlackRequest(channel_id="C1", thread_ts="1.0", text=text, thread_id=THREAD)


async def test_a_message_remembered_while_another_is_recalled_keeps_both() -> None:
    await index.create(THREAD, runtime="mda", metadata={"title": "kept"})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled, *_ = await asyncio.gather(
        mda_forwarding.recall(THREAD, "1.1", "msg_1"),
        *(mda_forwarding.remember(_request(f"B{n}"), None, f"2.{n}") for n in range(10)),
    )

    assert recalled is not None and recalled[0].text == "A"
    for n in range(10):
        later = await mda_forwarding.recall(THREAD, f"2.{n}", f"msg_{n}")
        assert later is not None and later[0].text == f"B{n}"
    thread = await index.get(THREAD)
    assert thread is not None and thread.metadata["title"] == "kept"


async def test_a_message_is_recalled_once() -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled = await asyncio.gather(
        *(mda_forwarding.recall(THREAD, "1.1", f"msg_{n}") for n in range(5))
    )

    assert [r[0].text for r in recalled if r is not None] == ["A"]


async def test_mda_retrying_a_call_gets_the_same_request_until_the_sweep_drops_it(
    monkeypatch: pytest.MonkeyPatch, replies: AsyncMock
) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_d1") is not None

    # The first answer never reached MDA (a 502, a dropped connection): it asks again.
    retried = await mda_forwarding.recall(THREAD, "1.1", "msg_d1")

    assert retried is not None and retried[0].text == "A"
    # Any other call still gets nothing, and the message the channel took isn't lost.
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_d2") is None
    assert await mda_forwarding.report_lost(THREAD, "1.1", "lost") is False
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 400)
    await mda_forwarding.sweep_lost_requests()
    replies.assert_not_awaited()
    thread = await index.get(THREAD)
    assert thread is not None and thread.metadata[mda_forwarding.RECALLED_KEY] == {}


async def test_expired_requests_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("old"), None, "1.1")
    monkeypatch.setattr(time, "time", lambda: 1e12)

    await mda_forwarding.remember(_request("new"), None, "2.1")

    thread = await index.get(THREAD)
    assert thread is not None
    assert list(thread.metadata[mda_forwarding.PENDING_KEY]) == ["2.1"]
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_1") is None


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
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_1") is None


async def test_a_message_the_channel_took_is_not_reported(replies: AsyncMock) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_1") is not None

    assert await mda_forwarding.report_lost(THREAD, "1.1", "lost") is False
    replies.assert_not_awaited()


async def test_a_refused_new_conversation_goes_back_to_the_backend(
    monkeypatch: pytest.MonkeyPatch, replies: AsyncMock
) -> None:
    local = AsyncMock()
    monkeypatch.setattr(type(router.RUNTIMES), "local", property(lambda _self: local))
    processed = AsyncMock()
    monkeypatch.setattr(mda_forwarding, "process_slack_mention", processed)
    thread_id = await mda_forwarding.claim_conversation("A1", "T1", "C1", "1.0", owner_login="ada")
    # No run on MDA wrote its metadata, so a refused forward hands it back.
    assert not await mda_forwarding.channel_started(thread_id)
    await mda_forwarding.remember(
        SlackRequest(channel_id="C1", thread_ts="1.0", text="A", thread_id=thread_id), None, "1.1"
    )

    taken = await asyncio.gather(*(mda_forwarding.take_back(thread_id, "1.1") for _ in range(3)))

    assert taken.count(True) == 1
    [(request, repo)] = [call.args for call in processed.await_args_list]
    assert (request.text, repo) == ("A", None)
    replies.assert_not_awaited()
    # The thread is the backend's now, so the conversation's later messages stay here.
    thread = await index.get(thread_id)
    assert thread is not None and mda_forwarding.channel_of(thread.metadata) is None
    local.update.assert_awaited_with(thread_id, {"channel": None})
    assert await mda_forwarding.owned_thread("C1", "1.0") is None
    # Its private stub is a placeholder still: the backend's first write sets who sees it.
    assert not visibility_is_settled(thread.metadata)


async def test_a_message_the_channel_took_is_not_taken_back(replies: AsyncMock) -> None:
    await index.create(THREAD, runtime="mda", metadata={"channel": "slack"})
    await mda_forwarding.remember(_request("A"), None, "1.1")
    assert await mda_forwarding.recall(THREAD, "1.1", "msg_1") is not None

    assert await mda_forwarding.take_back(THREAD, "1.1") is False


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
    thread_id = await mda_forwarding.claim_conversation("A1", "T1", "C9", "9.1", owner_login="ada")

    assert thread_id == mda_forwarding.mda_thread_id("A1", "T1", "C9", "9.1")
    thread = await index.get(thread_id)
    assert thread is not None
    assert thread.metadata["visibility"] == "private"
    assert thread.metadata["source"] == "slack"
    # The person who started it may prompt it; the run's own access check passes.
    assert thread.metadata["owner_login"] == "ada"
    assert ThreadRecord(exists=True, metadata=thread.metadata).authorize("ada")
    with pytest.raises(HTTPException):
        ThreadRecord(exists=True, metadata=thread.metadata).authorize("someone-else")
    assert thread.metadata["channel"] == "slack"
    local.create.assert_awaited_once_with(thread_id, thread.metadata)
    # Another app's conversation can't take the Slack thread now.
    with pytest.raises(SlackThreadMappingError):
        await mda_forwarding.claim_conversation("A2", "T1", "C9", "9.1")
