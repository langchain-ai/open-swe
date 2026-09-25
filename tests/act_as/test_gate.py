"""Asking a participant before a PR opens as them in a shared thread."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.act_as import gate
from agent.act_as.records import ThreadActAs
from agent.users import User, UserPreferences
from agent.utils.json_types import JsonObject
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY


@pytest.fixture
def dm(thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Alice's own run in a thread Bob also posted in."""

    async def author_login(requested: str | None = None) -> str:
        return requested or "alice"

    monkeypatch.setattr(gate, "pr_author_login", author_login)
    monkeypatch.setattr(gate, "open_slack_dm", AsyncMock(return_value=("D-ALICE", None)))
    send = AsyncMock(return_value=("123.456", None))
    monkeypatch.setattr(gate, "post_slack_top_level_message_with_ts", send)
    send.concierge = AsyncMock()
    monkeypatch.setattr(gate, "note_for_concierge", send.concierge)
    monkeypatch.setattr(gate, "_WAIT_SECONDS", 4.0)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    return send


def _alice(monkeypatch: pytest.MonkeyPatch, slack_id: str, *, always: bool = False) -> None:
    person = SimpleNamespace(
        slack_user_id=slack_id, typed_preferences=UserPreferences(act_as_always_allowed=always)
    )
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=person))


async def _open() -> gate.ActAsRefusal | None:
    return await gate.require_consent(
        thread_id="thread-1",
        token_kind="user",
        author=None,
        owner="o",
        repo="r",
        head="h",
        base="b",
        title="t",
    )


async def _answer(approved: bool) -> None:
    thread = await ThreadActAs.load("thread-1")
    request = thread.for_login("alice")
    assert request is not None
    await thread.decide(request, approved=approved, always_allow=False)


@pytest.mark.asyncio
async def test_shared_thread_asks_even_when_the_author_started_the_run(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE")

    refusal = await _open()

    assert refusal is not None and refusal["act_as"] == "pending"
    dm.assert_awaited_once()
    assert dm.await_args.args[0] == "D-ALICE"
    value = json.loads(dm.await_args.kwargs["blocks"][1]["elements"][0]["value"])
    assert (value["type"], value["action"], value["thread_id"]) == ("act_as", "approve", "thread-1")
    user_id, channel_id, note = dm.concierge.await_args.args
    assert (user_id, channel_id) == ("U-ALICE", "D-ALICE")
    assert '"t" in o/r' in note


@pytest.mark.asyncio
async def test_single_participant_thread_never_asks(dm, thread_metadata, monkeypatch):
    _alice(monkeypatch, "U-ALICE")
    thread_metadata[PARTICIPANT_LOGINS_KEY] = {"alice": True}

    assert await _open() is None
    dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_during_the_wait_lets_the_pr_open(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE")

    async def approve(_seconds: float) -> None:
        await _answer(True)

    monkeypatch.setattr(asyncio, "sleep", approve)

    assert await _open() is None


@pytest.mark.asyncio
async def test_denial_during_the_wait_refuses(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE")

    async def deny(_seconds: float) -> None:
        await _answer(False)

    monkeypatch.setattr(asyncio, "sleep", deny)

    refusal = await _open()

    assert refusal is not None and refusal["act_as"] == "denied"


@pytest.mark.asyncio
async def test_retry_after_a_late_approval_opens_without_asking_again(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE")
    timed_out = await _open()
    assert timed_out is not None and timed_out["act_as"] == "pending"
    await _answer(True)
    dm.reset_mock()

    assert await _open() is None
    dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_person_without_slack_is_never_acted_as(dm, monkeypatch):
    _alice(monkeypatch, "")

    refusal = await _open()

    assert refusal is not None and refusal["act_as"] == "unreachable"
    dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_dm_refuses_instead_of_waiting(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE")
    dm.return_value = (None, "channel_not_found")

    refusal = await _open()

    assert refusal is not None and refusal["act_as"] == "unreachable"
    dm.concierge.assert_not_awaited()


@pytest.mark.asyncio
async def test_always_allow_skips_the_dm(dm, monkeypatch):
    _alice(monkeypatch, "U-ALICE", always=True)

    assert await _open() is None
    dm.assert_not_awaited()
