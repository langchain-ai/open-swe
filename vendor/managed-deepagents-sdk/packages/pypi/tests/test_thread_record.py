"""The thread record codec: storage form, strict parsing and the participant rules."""

from __future__ import annotations

import json
from typing import Any

import pytest

from managed_deepagents._thread_record import (
    THREAD_PARTICIPANTS_METADATA,
    THREAD_RECORD_METADATA,
    ThreadRecord,
    is_person_principal,
    next_thread_record,
    parse_thread_record,
    target_from_origin,
    thread_record_metadata,
)

KEY = "slack:v1:A0123:T0456:C0789:1712345678.000100"
TRIGGER: Any = {
    "name": "slack",
    "provider": "slack",
    "source_thread_key": KEY,
    "delivery_id": "delivery-1",
    "transport": "trigger_server",
    "event": {"type": "message", "messages": [{"role": "user", "content": "secret"}]},
    "raw_event": {"text": "secret"},
    "target": {
        "channel_id": "8c1e",
        "address": {"conversation_id": "C0789"},
        "source_thread_key": KEY,
    },
}
DIRECT: Any = {**TRIGGER, "transport": "direct", "target": {"repository": "acme/app", "number": 7}}


@pytest.mark.parametrize("binding", [TRIGGER, DIRECT], ids=["trigger", "direct"])
def test_the_record_round_trips_without_the_event(binding: Any) -> None:
    metadata = thread_record_metadata(next_thread_record(None, binding, "alice"))
    assert "secret" not in json.dumps(metadata)
    record = parse_thread_record(metadata, exact=True)
    assert record is not None
    assert record.origin is not None
    assert target_from_origin(record.origin.channel) == binding["target"]
    assert metadata[THREAD_PARTICIPANTS_METADATA] == ["alice"]


@pytest.mark.parametrize(
    ("principal", "person"),
    [("alice", True), ("github:42", True), ("mda:channel", False), (" ", False), (None, False)],
)
def test_person_principals(principal: object, person: bool) -> None:
    assert is_person_principal(principal) is person


def test_the_first_person_owns_the_thread_and_services_are_not_participants() -> None:
    record = next_thread_record(None, TRIGGER, "mda:channel")
    assert record.origin is not None
    assert record.origin.sender is None
    for sender in ("alice", "bob", "alice"):
        record = next_thread_record(record, TRIGGER, sender)
    assert dict(record.participants) == {"alice": "owner", "bob": "member"}
    assert record.origin is not None
    assert record.origin.sender == "alice"


@pytest.mark.parametrize(
    "raw",
    [
        {"v": 2, "participants": {}},
        {"v": 1, "participants": {"alice": {"role": "admin"}}},
        {"v": 1, "participants": {}, "extra": True},
        "not a record",
    ],
)
def test_invalid_records_read_as_none(raw: object) -> None:
    assert parse_thread_record({THREAD_RECORD_METADATA: raw}) is None


def test_a_malformed_origin_keeps_the_participants_except_for_a_write() -> None:
    metadata = {
        THREAD_RECORD_METADATA: {
            "v": 1,
            "origin": {"channel": {"name": "slack"}},
            "participants": {"alice": {"role": "owner"}},
        },
        THREAD_PARTICIPANTS_METADATA: ["alice"],
    }
    assert parse_thread_record(metadata) == ThreadRecord(participants={"alice": "owner"})
    assert parse_thread_record(metadata, exact=True) is None
