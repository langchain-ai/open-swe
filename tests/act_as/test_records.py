"""Act-as requests kept in thread metadata."""

from unittest.mock import AsyncMock

import pytest

from openswe.act_as.records import ThreadActAs
from openswe.users import User, UserPreferences, UserPreferencesPatch
from openswe.utils.json_types import JsonObject
from openswe.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
)

_PR = {"owner": "o", "repo": "r", "head": "h", "base": "b", "title": "t"}


@pytest.mark.asyncio
async def test_each_pr_keeps_its_own_request_after_reload(thread_metadata: JsonObject) -> None:
    thread = await ThreadActAs.load("thread-1")
    first = await thread.request("alice", **_PR)
    retry = await thread.request("Alice", **{**_PR, "title": "renamed"})
    other = await thread.request("alice", **{**_PR, "head": "other"})
    await thread.decide(first, approved=True, always_allow=False)
    await thread.decide(other, approved=False, always_allow=False)

    reloaded = await ThreadActAs.load("thread-1")

    assert retry == first
    assert len(reloaded.requests) == 2
    assert reloaded.decision_for(first) == "approved"
    assert reloaded.decision_for(other) == "denied"


@pytest.mark.asyncio
async def test_an_unreadable_stored_record_is_dropped(thread_metadata: JsonObject) -> None:
    thread_metadata["act_as_decision:bad"] = {"decision": "maybe"}

    assert (await ThreadActAs.load("thread-1")).decisions == {}


@pytest.mark.asyncio
async def test_the_first_answer_stands(
    thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_metadata[PARTICIPANT_EMAILS_KEY] = {"alice@example.com": True}
    monkeypatch.setattr(
        User, "for_person", AsyncMock(side_effect=RuntimeError("Identity lookup unavailable"))
    )
    thread = await ThreadActAs.load("thread-1")
    request = await thread.request("alice", **_PR)
    assert await thread.decide(request, approved=False, always_allow=False)

    later = await ThreadActAs.load("thread-1")
    assert not await later.decide(request, approved=True, always_allow=False)
    assert (await ThreadActAs.load("thread-1")).decision_for(request) == "denied"


@pytest.mark.asyncio
async def test_a_late_notification_write_keeps_the_decision(thread_metadata: JsonObject) -> None:
    sender = await ThreadActAs.load("thread-1")
    request = await sender.request("alice", **_PR)
    clicker = await ThreadActAs.load("thread-1")
    await clicker.decide(request, approved=True, always_allow=False)

    await sender.mark_notified(request)

    assert (await ThreadActAs.load("thread-1")).decision_for(request) == "approved"


@pytest.mark.asyncio
async def test_always_allow_is_saved_on_the_person(
    thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = AsyncMock(return_value=UserPreferences(act_as_always_allowed=True))
    monkeypatch.setattr(User, "update_preferences", update)
    thread = await ThreadActAs.load("thread-1")
    request = await thread.request("alice", **_PR)

    await thread.decide(request, approved=True, always_allow=True)

    assert (await ThreadActAs.load("thread-1")).decision_for(request) == "approved"
    update.assert_awaited_once_with("alice", UserPreferencesPatch(act_as_always_allowed=True))
