from langchain_core.messages import AIMessage, HumanMessage

from openswe.utils.run_usage import summarize_run_usage


def _message(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int | None = 0,
    cache_creation_tokens: int | None = None,
) -> AIMessage:
    details = {
        key: value
        for key, value in (
            ("cache_read", cache_read_tokens),
            ("cache_creation", cache_creation_tokens),
        )
        if value is not None
    }
    return AIMessage(
        content="",
        response_metadata={"model_name": model},
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": details,
        },
    )


def test_summarize_run_usage_uses_only_latest_human_turn() -> None:
    state = {
        "messages": [
            HumanMessage(content="old"),
            _message(model="old-model", input_tokens=100, output_tokens=10),
            HumanMessage(content="current"),
            _message(model="model-a", input_tokens=1_000, output_tokens=100),
            _message(model="model-b", input_tokens=2_000, output_tokens=200),
        ]
    }

    summary = summarize_run_usage(state)

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.total_tokens == 3_300
    assert summary.input_tokens == 3_000
    assert summary.output_tokens == 300


def test_summarize_run_usage_rejects_conflicting_message_identifiers() -> None:
    message = _message(model="model-a", input_tokens=100, output_tokens=10)
    message.response_metadata.update(
        {"open_swe_invocation_id": "inv-1", "open_swe_run_id": "inv-2"}
    )

    assert summarize_run_usage({"messages": [message]}, invocation_id="inv-1") is None


def test_summarize_run_usage_excludes_cached_input_tokens() -> None:
    summary = summarize_run_usage(
        {
            "messages": [
                HumanMessage(content="current"),
                _message(
                    model="model-a",
                    input_tokens=1_000,
                    output_tokens=100,
                    cache_read_tokens=600,
                ),
            ]
        }
    )

    assert summary is not None
    assert summary.total_tokens == 500
    assert summary.input_tokens == 1_000
    assert summary.output_tokens == 100
    assert summary.cache_read_tokens == 600
    assert summary.cache_creation_tokens is None


def test_summarize_run_usage_sums_cache_tokens_and_keeps_unreported_unknown() -> None:
    summary = summarize_run_usage(
        {
            "messages": [
                HumanMessage(content="current"),
                _message(
                    model="model-a",
                    input_tokens=1_000,
                    output_tokens=100,
                    cache_read_tokens=600,
                    cache_creation_tokens=300,
                ),
                _message(
                    model="model-a",
                    input_tokens=500,
                    output_tokens=50,
                    cache_read_tokens=None,
                ),
                _message(
                    model="model-a",
                    input_tokens=2_000,
                    output_tokens=20,
                    cache_read_tokens=1_500,
                    cache_creation_tokens=0,
                ),
            ]
        }
    )

    assert summary is not None
    assert summary.total_tokens == 1_570
    assert summary.cache_read_tokens == 2_100
    assert summary.cache_creation_tokens == 300

    unreported = summarize_run_usage(
        {
            "messages": [
                HumanMessage(content="current"),
                _message(model="model-a", input_tokens=10, output_tokens=1, cache_read_tokens=None),
            ]
        }
    )

    assert unreported is not None
    assert unreported.cache_read_tokens is None
    assert unreported.cache_creation_tokens is None
