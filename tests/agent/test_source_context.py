import pytest

from agent.source_context import SourceContext


def test_round_trip_preserves_unknown_keys_exactly() -> None:
    raw = {
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        "breakout_from": {"channel_id": "C0", "message_ts": "0.1"},
        "some_future_key": ["anything"],
    }

    assert SourceContext.parse(raw).dump() == raw


def test_round_trip_does_not_inject_defaults_for_absent_fields() -> None:
    raw = {"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}}

    dumped = SourceContext.parse(raw).dump()

    assert "permalink" not in dumped["slack_thread"]
    assert "linear_issue" not in dumped


def test_enriching_a_field_adds_only_that_key() -> None:
    context = SourceContext.parse(
        {
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
            "breakout_from": {"channel_id": "C0"},
        }
    )

    assert context.slack_thread is not None
    context.slack_thread.permalink = "https://slack.example/x"

    assert context.dump() == {
        "slack_thread": {
            "channel_id": "C1",
            "thread_ts": "1.2",
            "permalink": "https://slack.example/x",
        },
        "breakout_from": {"channel_id": "C0"},
    }


@pytest.mark.parametrize("raw", [None, "not-a-mapping", 42, [], {"slack_thread": "wrong-type"}])
def test_parse_never_raises(raw: object) -> None:
    assert isinstance(SourceContext.parse(raw), SourceContext)


def test_from_metadata_tolerates_missing_and_malformed_metadata() -> None:
    assert SourceContext.from_metadata(None).is_empty
    assert SourceContext.from_metadata({}).is_empty
    assert SourceContext.from_metadata({"source_context": None}).is_empty


def test_slack_location_requires_both_halves() -> None:
    assert SourceContext.parse({"slack_thread": {"channel_id": "C1"}}).slack_location is None
    assert SourceContext.parse(
        {"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}}
    ).slack_location == ("C1", "1.2")


def test_get_reads_declared_fields_and_extras() -> None:
    context = SourceContext.parse({"linear_issue": {"id": "lin-1"}, "custom": "x"})

    assert context.get("custom") == "x"
    assert context.get("linear_issue") is not None
    assert context.get("missing") is None
