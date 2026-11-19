import logging
import socket
from collections.abc import Iterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from openswe.utils.model_usage_metrics import CallPosition, ModelUsageMetrics


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


@pytest.mark.parametrize(
    ("position", "input_tokens", "cache_read", "cache_creation"),
    [
        ("first", 30_000, 20_000, 4_000),
        ("followup", 366_991, 0, 0),
        ("followup", 377_821, 366_992, 0),
        ("followup", 381_309, 377_822, 0),
    ],
)
def test_a_reply_counts_its_uncached_input_apart_from_the_cache(
    dogstatsd: socket.socket,
    caplog: pytest.LogCaptureFixture,
    position: CallPosition,
    input_tokens: int,
    cache_read: int,
    cache_creation: int,
) -> None:
    """``input_tokens`` includes the cache's tokens: the uncached part is what is left."""
    caplog.set_level(logging.INFO, logger="openswe.utils.model_usage_metrics")
    reply = AIMessage(
        id="reply-1",
        content="private reply content",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": 10,
            "total_tokens": input_tokens + 10,
            "input_token_details": {
                "cache_read": cache_read,
                "cache_creation": cache_creation,
            },
        },
        response_metadata={
            "model_provider": "google_genai",
            "model_name": "gemini-4-carbon",
            "open_swe_invocation_id": "invocation-1",
        },
    )

    ModelUsageMetrics.record(
        [HumanMessage(content="private prompt"), AIMessage(content="no usage"), reply],
        position=position,
        thread_id="thread-1",
    )

    tags = f"openswe_env:production,provider:google_genai,model:gemini-4-carbon,call_position:{position}"
    uncached = input_tokens - cache_read - cache_creation
    assert dogstatsd.recv(4096).decode().splitlines() == [
        f"openswe_model_usage_tokens:{uncached}|c|#token_type:input,{tags}",
        f"openswe_model_usage_tokens:{cache_read}|c|#token_type:cache_read,{tags}",
        f"openswe_model_usage_tokens:{cache_creation}|c|#token_type:cache_creation,{tags}",
        f"openswe_model_usage_calls:1|c|#{tags}",
    ]
    observed = [r for r in caplog.records if r.message == "Model usage observed"]
    assert len(observed) == 2
    assert observed[0].__dict__["usage_present"] is False
    assert observed[0].__dict__["input_tokens"] == 0
    assert (
        observed[1].__dict__.items()
        >= {
            "usage_present": True,
            "input_tokens": input_tokens,
            "uncached_input_tokens": uncached,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "provider": "google_genai",
            "model": "gemini-4-carbon",
            "call_position": position,
            "openswe_env": "production",
            "thread_id": "thread-1",
            "reply_id": "reply-1",
            "invocation_id": "invocation-1",
        }.items()
    )
    assert "private" not in caplog.text


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
        f"openswe_model_usage_calls:1|c|#{tags}",
    ]


@pytest.mark.parametrize("agent", ["unconfigured", "bad_host", "closed_socket"])
def test_a_reply_never_raises_into_its_report_without_an_agent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    agent: str,
) -> None:
    """Usage stays observable, while only configured transport failures warn."""
    monkeypatch.setattr(ModelUsageMetrics, "_socket", None)
    for key in ("DD_API_KEY", "DD_DOGSTATSD_URL", "DD_AGENT_HOST", "DD_DOGSTATSD_PORT"):
        monkeypatch.delenv(key, raising=False)
    sender = None
    if agent == "bad_host":
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        monkeypatch.setattr(socket, "socket", lambda *_args: sender)
        monkeypatch.setenv("DD_AGENT_HOST", "not a host")
    elif agent == "closed_socket":
        monkeypatch.setenv("DD_API_KEY", "configured")
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.close()
        monkeypatch.setattr(ModelUsageMetrics, "_socket", sender)
    caplog.set_level(logging.INFO, logger="openswe.utils.model_usage_metrics")
    reply = AIMessage(
        id="reply-1",
        content="done",
        usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
    )

    ModelUsageMetrics.record([reply], position="followup", thread_id="thread-1")

    observed = [r for r in caplog.records if r.message == "Model usage observed"]
    assert len(observed) == 1
    assert observed[0].__dict__["uncached_input_tokens"] == 5
    failures = [r for r in caplog.records if r.levelno >= logging.WARNING]
    if agent == "unconfigured":
        assert not failures
        assert ModelUsageMetrics._socket is None
    else:
        assert sender is not None and sender.fileno() == -1
        assert len(failures) == 1
        assert failures[0].message == "Model usage metrics were not sent"
        assert failures[0].exc_info is not None
        assert failures[0].__dict__["thread_id"] == "thread-1"
        assert failures[0].__dict__["reply_id"] == "reply-1"
