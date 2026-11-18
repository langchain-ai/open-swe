import socket
from collections.abc import Iterator

import pytest
from langchain_core.messages import AIMessage

from openswe.utils.model_usage_metrics import ModelUsageMetrics


@pytest.fixture
def dogstatsd(monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    """A DogStatsD stand-in on loopback, which the process's socket sends to."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    monkeypatch.delenv("DD_DOGSTATSD_URL", raising=False)
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    monkeypatch.setenv("DD_DOGSTATSD_PORT", str(listener.getsockname()[1]))
    monkeypatch.setenv("OPENSWE_ENV", "production")
    monkeypatch.setattr(ModelUsageMetrics, "_socket", None)
    yield listener
    listener.close()
    if ModelUsageMetrics._socket is not None:
        ModelUsageMetrics._socket.close()


def test_a_reply_counts_its_uncached_input_apart_from_the_cache(
    dogstatsd: socket.socket,
) -> None:
    """``input_tokens`` includes the cache's tokens: the uncached part is what is left."""
    reply = AIMessage(
        content="done",
        usage_metadata={
            "input_tokens": 30_000,
            "output_tokens": 10,
            "total_tokens": 30_010,
            "input_token_details": {"cache_read": 20_000, "cache_creation": 4_000},
        },
        response_metadata={"model_provider": "anthropic", "model_name": "claude-opus-5-5"},
    )

    ModelUsageMetrics.record([reply], position="first")

    tags = "openswe_env:production,provider:anthropic,model:claude-opus-5-5,call_position:first"
    assert dogstatsd.recv(4096).decode().splitlines() == [
        f"openswe_model_usage_tokens:6000|c|#token_type:input,{tags}",
        f"openswe_model_usage_tokens:20000|c|#token_type:cache_read,{tags}",
        f"openswe_model_usage_tokens:4000|c|#token_type:cache_creation,{tags}",
    ]


def test_an_uncached_reply_still_counts_zero_cache_tokens(
    dogstatsd: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group that never hits the cache keeps every token type's series, at zero."""
    monkeypatch.setenv("OPENSWE_ENV", "prod")
    reply = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 30_000, "output_tokens": 10, "total_tokens": 30_010},
        response_metadata={"model_provider": "openai", "model_name": "gpt-6.1"},
    )

    ModelUsageMetrics.record([reply], position="first")

    tags = "openswe_env:production,provider:openai,model:gpt-6.1,call_position:first"
    assert dogstatsd.recv(4096).decode().splitlines() == [
        f"openswe_model_usage_tokens:30000|c|#token_type:input,{tags}",
        f"openswe_model_usage_tokens:0|c|#token_type:cache_read,{tags}",
        f"openswe_model_usage_tokens:0|c|#token_type:cache_creation,{tags}",
    ]


def test_a_reply_never_raises_into_its_report_without_an_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing listening, as off Datadog, the counts are dropped."""
    monkeypatch.setattr(ModelUsageMetrics, "_socket", None)
    monkeypatch.setenv("DD_AGENT_HOST", "not a host")
    reply = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
    )

    ModelUsageMetrics.record([reply], position="followup")
