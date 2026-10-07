"""Act-as requests kept in thread metadata."""

from unittest.mock import AsyncMock

import pytest

from openswe.act_as.records import ActAsRequest, ThreadActAs
from openswe.users import User, UserPreferences, UserPreferencesPatch
from openswe.utils.json_types import JsonObject
from openswe.utils.thread_participants import PARTICIPANT_EMAILS_KEY, PARTICIPANT_LOGINS_KEY

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
@pytest.mark.parametrize(
    ("logins", "emails", "shared"),
    [
        ({"alice": True}, {}, False),
        ({"alice": True, "bob": True}, {}, True),
        ({"alice": True}, {"carol@example.com": True}, True),
    ],
)
async def test_unlinked_participants_make_a_thread_shared(
    thread_metadata: JsonObject, logins: JsonObject, emails: JsonObject, shared: bool
) -> None:
    thread_metadata[PARTICIPANT_LOGINS_KEY] = logins
    thread_metadata[PARTICIPANT_EMAILS_KEY] = emails

    assert (await ThreadActAs.load("thread-1")).is_shared is shared


@pytest.mark.asyncio
async def test_an_unreadable_stored_record_is_dropped(thread_metadata: JsonObject) -> None:
    thread_metadata["act_as_decision:bad"] = {"decision": "maybe"}

    assert (await ThreadActAs.load("thread-1")).decisions == {}


@pytest.mark.asyncio
async def test_the_first_answer_stands(thread_metadata: JsonObject) -> None:
    thread = await ThreadActAs.load("thread-1")
    request = await thread.request("alice", **_PR)
    assert await thread.decide(request, approved=False, always_allow=False)

    later = await ThreadActAs.load("thread-1")
    assert not await later.decide(request, approved=True, always_allow=False)
    assert (await ThreadActAs.load("thread-1")).decision_for("alice") == "denied"


@pytest.mark.asyncio
async def test_a_late_notification_write_keeps_the_decision(thread_metadata: JsonObject) -> None:
    sender = await ThreadActAs.load("thread-1")
    request = await sender.request("alice", **_PR)
    clicker = await ThreadActAs.load("thread-1")
    await clicker.decide(request, approved=True, always_allow=False)

    await sender.mark_notified(request)

    assert (await ThreadActAs.load("thread-1")).decision_for("alice") == "approved"


@pytest.mark.asyncio
async def test_always_allow_is_saved_on_the_person(
    thread_metadata: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = AsyncMock(return_value=UserPreferences(act_as_always_allowed=True))
    monkeypatch.setattr(User, "update_preferences", update)
    thread = await ThreadActAs.load("thread-1")
    request = await thread.request("alice", **_PR)

    await thread.decide(request, approved=True, always_allow=True)

    assert (await ThreadActAs.load("thread-1")).decision_for("alice") == "approved"
    update.assert_awaited_once_with("alice", UserPreferencesPatch(act_as_always_allowed=True))
