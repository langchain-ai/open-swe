from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.human_review.notices import ReviewNotice
from openswe.slack import import_records
from openswe.slack.code_channels import delete_block_suggestions, store_block_suggestions
from openswe.slack.dm import DmOrigin
from openswe.slack.import_records import import_store_slack_mappings, import_store_slack_state
from openswe.slack.records import (
    SlackMessage,
    SlackThread,
    SlackViewSuggestion,
    bind_slack_thread_id,
    lookup_slack_run_mapping,
    lookup_slack_run_message_mapping,
    lookup_slack_thread_id,
    lookup_slack_thread_run_mapping,
    resolve_slack_thread_id,
    store_slack_run_mapping,
)
from openswe.store import StoreEntry
from openswe.thread_ids import slack_thread_id
from openswe.utils.json_types import JsonObject

_SAVED = datetime(2026, 1, 1, tzinfo=UTC)
_OPTION = {"text": {"type": "plain_text", "text": "open-swe"}, "value": "open-swe"}
_PICK_NOTICE = ReviewNotice(
    kind="reviewer_pick",
    review_request_id="0198",
    pr_url="https://github.com/acme/widgets/pull/7",
    text="Open SWE picked you to review acme/widgets#7.",
    target_thread_id="thread-impl",
)


class _Store:
    """The Store as the import reads it: searched by namespace prefix, with save times."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], tuple[JsonObject, datetime]] = {}

    def seed(
        self, namespace: str | tuple[str, ...], key: str, value: JsonObject, seconds: int = 0
    ) -> None:
        location = (namespace, "C1") if isinstance(namespace, str) else namespace
        self.items[(location, key)] = (value, _SAVED + timedelta(seconds=seconds))

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


class _Threads:
    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _Client:
    threads = _Threads()


@pytest.fixture
def store(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> _Store:
    fake = _Store()
    monkeypatch.setattr(import_records, "search_all_entries", fake.search)
    monkeypatch.setattr(import_records, "delete_value", fake.delete)
    return fake


def _retire_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    monkeypatch.setattr(import_records, "RETIRE_STORE_COPY_AFTER", timedelta())


async def test_store_import_copies_thread_and_run_mappings_then_retires_the_store_records(
    store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = {"thread_ts": "1.0", "triggering_user_id": "U1", "agent_thread_id": "thread-1"}
    store.seed("slack_thread_map", "1.0", {"thread_id": "thread-1"})
    store.seed("slack_thread_map", "2.0", {"nonce": "detached"})
    store.seed("slack_thread_map", "3.0", {"thread_id": "older-thread"})
    await bind_slack_thread_id("C1", "3.0", "thread-3")
    store.seed("slack_run_map", "run:run-1", {**run, "run_id": "run-1", "message_ts": "1.1"}, 1)
    store.seed("slack_run_map", "message:1.1", {**run, "run_id": "run-1", "message_ts": "1.1"}, 1)
    store.seed(
        "slack_run_map", "thread:1.0", {**run, "run_id": "run-2", "trace_message_ts": "1.05"}, 2
    )
    store.seed("slack_run_map", "run:run-2", {**run, "run_id": "run-2"}, 2)

    assert await import_store_slack_mappings() == StoreImport(moved=5, waiting=1)
    assert await lookup_slack_thread_id("C1", "1.0") == "thread-1"
    assert await lookup_slack_thread_id("C1", "3.0") == "thread-3"
    detached: Any = _Client()
    assert await resolve_slack_thread_id(detached, "C1", "2.0") == slack_thread_id(
        "C1", "2.0", "detached"
    )
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None
    assert (current.run_id, current.trace_message_ts) == ("run-2", "1.05")
    first = await lookup_slack_run_message_mapping("C1", "run-1")
    assert first is not None and first.message_ts == "1.1"
    message = await lookup_slack_run_mapping("C1", "1.1")
    assert message is not None
    assert (message.run_id, message.triggering_user_id, message.agent_thread_id) == (
        "run-1",
        "U1",
        "thread-1",
    )
    assert await import_store_slack_mappings() == StoreImport(waiting=6)

    store.save(("slack_run_map", "C1"), "thread:1.0", {**run, "run_id": "run-3"}, age=timedelta())
    store.save(("slack_run_map", "C1"), "run:run-3", {**run, "run_id": "run-3"}, age=timedelta())
    assert await import_store_slack_mappings() == StoreImport(moved=1, waiting=6)
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "run-3"

    _retire_immediately(monkeypatch)
    assert await import_store_slack_mappings() == StoreImport(moved=8)
    assert store.items == {}
    assert await lookup_slack_thread_id("C1", "1.0") == "thread-1"


async def test_store_import_merges_thread_and_message_state_into_their_rows(
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
        {
            "channel_id": "C1",
            "thread_ts": "1.0",
            "subject": "review",
            "notice": _PICK_NOTICE.model_dump(mode="json"),
        },
        age=hour,
    )
    store.save(("slack_reaction_events", "C1"), "Ev1", {"event_id": "Ev1"}, age=hour)
    await SlackThread.track_solo("C1", "3.0", "U1", 4.0)

    assert await import_store_slack_state() == StoreImport(moved=6, waiting=1)
    assert await SlackMessage.toggle_reaction("C1", "2.0", "U1", "-1", added=True) == {"+1", "-1"}
    first = await SlackThread.find("C1", "1.0")
    assert first is not None and first.solo_blocked
    assert first.kickoff_recorded and first.kickoff_ts == "1.1"
    armed = await SlackThread.find("C1", "3.0")
    assert armed is not None and armed.solo_last_seen == 4.0
    assert await SlackThread.find("C1", "0.5") is None
    assert await DmOrigin.of("D1", "5.0") == DmOrigin(
        channel_id="C1", thread_ts="1.0", subject="review", notice=_PICK_NOTICE
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
    armed = await SlackThread.find("C1", "3.0")
    assert armed is not None and armed.solo_blocked and armed.solo_user_id is None

    _retire_immediately(monkeypatch)
    assert await import_store_slack_state() == StoreImport(moved=5)
    assert store.items == {}


async def test_store_import_drops_mappings_an_old_replica_deleted_from_the_store(
    store: _Store,
) -> None:
    run = {"thread_ts": "1.0", "triggering_user_id": "U1"}
    store.seed("slack_run_map", "thread:1.0", {**run, "run_id": "run-1"})
    store.seed("slack_run_map", "run:run-1", {**run, "run_id": "run-1", "message_ts": "1.1"})
    store.seed("slack_run_map", "message:1.1", {**run, "run_id": "run-1"})
    store.seed("slack_run_map", "message:1.2", {**run, "run_id": "run-2"})
    store.seed("slack_run_map", "message:1.3", {**run, "run_id": "run-1"})
    store.seed(
        "slack_reaction_state",
        "run-1:U1:1.3",
        {"user_id": "U1", "message_ts": "1.3", "reactions": ["+1"]},
    )
    assert await import_store_slack_mappings() == StoreImport(moved=4)
    assert await import_store_slack_state() == StoreImport(moved=1)
    await store_slack_run_mapping("C1", "1.0", "run-2", message_ts="1.2")

    store.items.clear()

    assert await import_store_slack_mappings() == StoreImport(moved=3)
    assert await lookup_slack_run_message_mapping("C1", "run-1") is None
    assert await lookup_slack_run_mapping("C1", "1.1") is None
    assert await lookup_slack_run_mapping("C1", "1.3") is None
    assert await SlackMessage.toggle_reaction("C1", "1.3", "U1", "-1", added=True) == {"+1", "-1"}
    current = await lookup_slack_thread_run_mapping("C1", "1.0")
    assert current is not None and current.run_id == "run-2"
    message = await lookup_slack_run_mapping("C1", "1.2")
    assert message is not None and message.run_id == "run-2"


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
    assert await SlackViewSuggestion.actions_for("C1", "V1") == {}
    assert await SlackViewSuggestion.actions_for("C1", "V2") == {"repo": [_OPTION]}
    assert await SlackViewSuggestion.actions_for("C1", "V3") == {}


async def test_a_dm_keeps_the_review_notice_it_carried(registry_db: None) -> None:
    origin = DmOrigin(channel_id="C1", thread_ts="1.0", subject="review", notice=_PICK_NOTICE)

    await origin.save_for("D1", "6.0", "unused: the notice already has its text")

    assert await DmOrigin.of("D1", "6.0") == origin
