from langchain_core.messages import AIMessage, HumanMessage

from agent.utils.run_usage import summarize_run_usage


def _message(
    *, model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
) -> AIMessage:
    return AIMessage(
        content="",
        response_metadata={"model_name": model},
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read_tokens},
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


def test_summarize_run_usage_uses_run_id_across_human_messages() -> None:
    first = _message(model="model-a", input_tokens=100, output_tokens=10)
    first.response_metadata["open_swe_run_id"] = "run-1"
    second = _message(model="model-a", input_tokens=200, output_tokens=20)
    second.response_metadata["open_swe_run_id"] = "run-1"
    other_run = _message(model="model-b", input_tokens=400, output_tokens=40)
    other_run.response_metadata["open_swe_run_id"] = "run-2"

    summary = summarize_run_usage(
        {
            "messages": [
                HumanMessage(content="start"),
                first,
                HumanMessage(content="queued follow-up"),
                second,
                other_run,
            ]
        },
        run_id="run-1",
    )

    assert summary is not None
    assert summary.models == ("model-a",)
    assert summary.total_tokens == 330


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


def test_summarize_run_usage_ignores_messages_without_usage() -> None:
    complete = _message(model="model-a", input_tokens=100, output_tokens=10)
    incomplete = AIMessage(content="", response_metadata={"model_name": "model-b"})

    summary = summarize_run_usage(
        {"messages": [HumanMessage(content="current"), complete, incomplete]}
    )

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.total_tokens == 110


def test_summarize_run_usage_returns_none_without_reported_usage_or_model() -> None:
    assert (
        summarize_run_usage({"messages": [HumanMessage(content="hi"), AIMessage(content="")]})
        is None
    )
