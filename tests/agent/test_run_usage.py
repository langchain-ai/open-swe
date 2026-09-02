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
    assert summary.main_agent_tokens == 3_300


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
    assert summary.main_agent_tokens == 500


def test_summarize_run_usage_ignores_messages_without_usage() -> None:
    complete = _message(model="model-a", input_tokens=100, output_tokens=10)
    incomplete = AIMessage(content="", response_metadata={"model_name": "model-b"})

    summary = summarize_run_usage(
        {"messages": [HumanMessage(content="current"), complete, incomplete]}
    )

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.main_agent_tokens == 110


def test_summarize_run_usage_returns_none_without_reported_usage_or_model() -> None:
    assert (
        summarize_run_usage({"messages": [HumanMessage(content="hi"), AIMessage(content="")]})
        is None
    )
