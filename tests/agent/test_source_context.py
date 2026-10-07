from openswe.source_context import SourceContext


def test_round_trip_preserves_unknown_keys_exactly() -> None:
    raw = {
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        "breakout_from": {"channel_id": "C0", "message_ts": "0.1"},
        "some_future_key": ["anything"],
    }

    assert SourceContext.parse(raw).dump() == raw


def test_from_metadata_tolerates_missing_and_malformed_metadata() -> None:
    assert SourceContext.from_metadata(None).is_empty
    assert SourceContext.from_metadata({}).is_empty
    assert SourceContext.from_metadata({"source_context": None}).is_empty
