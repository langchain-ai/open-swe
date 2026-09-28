"""Tests for provider streaming timeout configuration."""

from unittest.mock import patch

from agent.utils.model import (
    DEFAULT_OPENAI_STREAM_CHUNK_TIMEOUT_SECONDS,
    make_model,
)


def test_openai_stream_chunk_timeout_defaults_and_is_overridable(monkeypatch) -> None:
    with patch("agent.utils.model.init_chat_model") as init_chat_model:
        make_model("openai:gpt-6-sol", use_gateway=False)
        default_kwargs = init_chat_model.call_args.kwargs

    assert default_kwargs["stream_chunk_timeout"] == DEFAULT_OPENAI_STREAM_CHUNK_TIMEOUT_SECONDS

    monkeypatch.setenv("LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S", "12")
    with patch("agent.utils.model.init_chat_model") as init_chat_model:
        make_model("openai:gpt-6-sol", use_gateway=False)
        assert init_chat_model.call_args.kwargs["stream_chunk_timeout"] == 12.0
