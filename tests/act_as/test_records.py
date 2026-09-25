"""Act-as requests kept in thread metadata."""

from unittest.mock import AsyncMock

import pytest

from agent.act_as.records import ACT_AS_KEY, ActAsRequest, ThreadActAs
from agent.users import User, UserPreferences, UserPreferencesPatch
from agent.utils.json_types import JsonObject
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY

_PR = {"owner": "o", "repo": "r", "head": "h", "base": "b", "title": "t"}


def test_fingerprint_is_per_thread_and_login() -> None:
    fingerprint = ActAsRequest.fingerprint_for("t1", "alice")
    assert ActAsRequest.fingerprint_for("t1", "Alice") == fingerprint
    assert ActAsRequest.fingerprint_for("t2", "alice") != fingerprint
    assert ActAsRequest.fingerprint_for("t1", "carol") != fingerprint


@pytest.mark.asyncio
async def test_one_request_per_person_survives_a_reload(thread_metadata: JsonObject) -> None:
    thread = await ThreadActAs.load("thread-1")
    first = await thread.request("alice", **_PR)
    await thread.request("Alice", **{**_PR, "head": "other"})

    reloaded = await ThreadActAs.load("thread-1")

    assert list(reloaded.requests) == [first.fingerprint]
    assert reloaded.for_login("alice") == first


@pytest.mark.asyncio
async def test_a_single_participant_thread_is_not_shared(thread_metadata: JsonObject) -> None:
    thread_metadata[PARTICIPANT_LOGINS_KEY] = {"alice": True}

    assert not (await ThreadActAs.load("thread-1")).is_shared


@pytest.mark.asyncio
async def test_an_unreadable_stored_request_is_dropped(thread_metadata: JsonObject) -> None:
    thread_metadata[ACT_AS_KEY] = {"bad": {"status": "maybe"}}

    assert (await ThreadActAs.load("thread-1")).requests == {}


@pytest.mark.asyncio
async def test_always_allow_is_saved_on_the_person(
    thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = AsyncMock(return_value=UserPreferences(act_as_always_allowed=True))
    monkeypatch.setattr(User, "update_preferences", update)
    thread = await ThreadActAs.load("thread-1")
    request = await thread.request("alice", **_PR)

    await thread.decide(request, approved=True, always_allow=True)

    decided = (await ThreadActAs.load("thread-1")).for_login("alice")
    assert decided is not None and decided.status == "approved"
    update.assert_awaited_once_with("alice", UserPreferencesPatch(act_as_always_allowed=True))
