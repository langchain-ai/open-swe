from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.slack import import_state
from openswe.slack.code_channels import delete_block_suggestions, store_block_suggestions
from openswe.slack.dm import DmOrigin
from openswe.slack.import_state import import_store_slack_state
from openswe.slack.records import (
    SlackKickoff,
    SlackReactionState,
    SlackSoloThread,
    SlackViewSuggestions,
)
from openswe.store import StoreEntry
from openswe.utils.json_types import JsonObject

_OPTION = {"text": {"type": "plain_text", "text": "open-swe"}, "value": "open-swe"}


class _Store:
    """The Store as the import reads it: searched by namespace prefix, with save times."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], tuple[JsonObject, datetime]] = {}

    def save(
        self, namespace: tuple[str, ...], key: str, value: JsonObject, *, age: timedelta
    ) -> None:
        self.items[(namespace, key)] = (value, datetime.now(UTC) - age)

    async def search(self, namespace: Sequence[str]) -> list[StoreEntry]:
        return [
            StoreEntry(list(item_namespace), dict(value), key, saved_at)
            for (item_namespace, key), (value, saved_at) in self.items.items()
            if item_namespace[: len(namespace)] == tuple(namespace)
        ]

    async def delete(self, namespace: Sequence[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


@pytest.fixture
def store(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> _Store:
    fake = _Store()
    monkeypatch.setattr(import_state, "search_all_entries", fake.search)
    monkeypatch.setattr(import_state, "delete_value", fake.delete)
    return fake


async def test_store_import_copies_slack_state_then_retires_the_store_records(
    store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    hour = timedelta(hours=1)
    store.save(
        ("slack_reaction_state", "C1"),
        "run-1:U1:2.0",
        {"run_id": "run-1", "user_id": "U1", "message_ts": "2.0", "reactions": ["+1"]},
        age=hour,
    )
    store.save(("slack_solo_threads", "C1"), "1.0", {"blocked": True}, age=hour)
    store.save(("slack_solo_threads", "C1"), "3.0", {"user_id": "U1", "last_seen": 3.5}, age=hour)
    store.save(("slack_kickoff", "C1"), "1.0", {"kickoff_ts": "1.1"}, age=hour)
    store.save(("slack_kickoff", "C1"), "0.5", {"kickoff_ts": "0.6"}, age=timedelta(days=30))
    store.save(
        ("slack_dm_origin", "D1"),
        "5.0",
        {"channel_id": "C1", "thread_ts": "1.0", "subject": "review"},
        age=hour,
    )
    store.save(("slack_reaction_events", "C1"), "Ev1", {"event_id": "Ev1"}, age=hour)
    await SlackSoloThread(channel_id="C1", thread_ts="3.0", user_id="U1", last_seen=4.0).save()

    assert await import_store_slack_state() == StoreImport(moved=6, waiting=1)
    assert await SlackReactionState.toggle("C1", "2.0", "U1", "-1", added=True) == {"+1", "-1"}
    blocked = await SlackSoloThread.find("C1", "1.0")
    assert blocked is not None and blocked.blocked
    armed = await SlackSoloThread.find("C1", "3.0")
    assert armed is not None and armed.last_seen == 4.0
    kickoff = await SlackKickoff.find("C1", "1.0")
    assert kickoff is not None and kickoff.kickoff_ts == "1.1"
    assert await SlackKickoff.find("C1", "0.5") is None
    assert await DmOrigin.of("D1", "5.0") == DmOrigin(
        channel_id="C1", thread_ts="1.0", subject="review"
    )
    assert set(store.items) == {
        (("slack_reaction_state", "C1"), "run-1:U1:2.0"),
        (("slack_solo_threads", "C1"), "1.0"),
        (("slack_solo_threads", "C1"), "3.0"),
        (("slack_kickoff", "C1"), "1.0"),
        (("slack_dm_origin", "D1"), "5.0"),
    }

    store.save(("slack_solo_threads", "C1"), "3.0", {"blocked": True}, age=timedelta())
    assert await import_store_slack_state() == StoreImport(moved=1, waiting=4)
    armed = await SlackSoloThread.find("C1", "3.0")
    assert armed is not None and armed.blocked

    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_slack_state() == StoreImport(moved=5)
    assert store.items == {}


async def test_store_import_drops_view_suggestions_an_old_replica_deleted_from_the_store(
    store: _Store,
) -> None:
    namespace = ("slack_code_channel_view_suggestions", "C1")
    for view_id in ("V1", "V2", "V3"):
        store.save(
            (*namespace, view_id), "suggestions", {"actions": {"repo": [_OPTION]}}, age=timedelta()
        )
    assert await import_store_slack_state() == StoreImport(moved=3)
    await store_block_suggestions("C1", "V2", {"repo": [_OPTION]})
    await delete_block_suggestions("C1", "V3")
    await store.delete((*namespace, "V1"), "suggestions")

    assert await import_store_slack_state() == StoreImport(moved=1, waiting=1)
    assert await SlackViewSuggestions.actions_for("C1", "V1") == {}
    assert await SlackViewSuggestions.actions_for("C1", "V2") == {"repo": [_OPTION]}
    assert await SlackViewSuggestions.actions_for("C1", "V3") == {}
