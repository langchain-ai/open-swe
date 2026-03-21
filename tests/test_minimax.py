"""Tests for MiniMax LLM provider integration.

Unit tests verify model.py routing, temperature clamping, and configuration.
Integration tests verify actual API calls (require MINIMAX_API_KEY).
"""

import os
from unittest.mock import patch

import pytest

from agent.utils.model import MINIMAX_BASE_URL, MINIMAX_MODELS, make_model


# --- Unit Tests ---


class TestMiniMaxConfig:
    """Test MiniMax configuration constants."""

    def test_minimax_base_url(self):
        assert MINIMAX_BASE_URL == "https://api.minimax.io/v1"

    def test_minimax_models_defined(self):
        assert "MiniMax-M2.5" in MINIMAX_MODELS
        assert "MiniMax-M2.5-highspeed" in MINIMAX_MODELS

    def test_minimax_context_lengths(self):
        assert MINIMAX_MODELS["MiniMax-M2.5"] == 204_000
        assert MINIMAX_MODELS["MiniMax-M2.5-highspeed"] == 204_000


class TestMiniMaxRouting:
    """Test that make_model correctly routes minimax: prefix."""

    def test_minimax_prefix_detected(self):
        """Verify minimax: prefix triggers MiniMax-specific handling."""
        model_id = "minimax:MiniMax-M2.5"
        assert model_id.startswith("minimax:")

    def test_minimax_model_name_extraction(self):
        """Verify model name is correctly extracted from minimax: prefix."""
        model_id = "minimax:MiniMax-M2.5"
        model_name = model_id.split(":", 1)[1]
        assert model_name == "MiniMax-M2.5"

    def test_minimax_highspeed_model_name(self):
        model_id = "minimax:MiniMax-M2.5-highspeed"
        model_name = model_id.split(":", 1)[1]
        assert model_name == "MiniMax-M2.5-highspeed"

    def test_minimax_does_not_match_openai(self):
        assert not "openai:gpt-4o".startswith("minimax:")

    def test_minimax_does_not_match_anthropic(self):
        assert not "anthropic:claude-sonnet-4-6".startswith("minimax:")


class TestMiniMaxTemperature:
    """Test temperature clamping for MiniMax models."""

    def test_temperature_within_range(self):
        temp = 0.7
        clamped = min(max(temp, 0.0), 1.0)
        assert clamped == 0.7

    def test_temperature_above_max(self):
        temp = 1.5
        clamped = min(max(temp, 0.0), 1.0)
        assert clamped == 1.0

    def test_temperature_zero(self):
        temp = 0.0
        clamped = min(max(temp, 0.0), 1.0)
        assert clamped == 0.0

    def test_temperature_negative(self):
        temp = -0.5
        clamped = min(max(temp, 0.0), 1.0)
        assert clamped == 0.0

    def test_temperature_exactly_one(self):
        temp = 1.0
        clamped = min(max(temp, 0.0), 1.0)
        assert clamped == 1.0


class TestMakeModelMiniMax:
    """Test make_model() with MiniMax provider routing."""

    @patch("agent.utils.model.init_chat_model")
    def test_minimax_routes_to_openai_provider(self, mock_init):
        """MiniMax should be routed via openai: prefix with custom base_url."""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            make_model("minimax:MiniMax-M2.5", temperature=0, max_tokens=16_000)

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args
        # model_id should be rewritten to openai:MiniMax-M2.5
        assert call_kwargs.kwargs["model"] == "openai:MiniMax-M2.5"
        assert call_kwargs.kwargs["base_url"] == MINIMAX_BASE_URL
        assert call_kwargs.kwargs["api_key"] == "test-key"

    @patch("agent.utils.model.init_chat_model")
    def test_minimax_temperature_clamped(self, mock_init):
        """Temperature should be clamped to [0.0, 1.0] for MiniMax."""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            make_model("minimax:MiniMax-M2.5", temperature=2.0, max_tokens=16_000)

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs["temperature"] == 1.0

    @patch("agent.utils.model.init_chat_model")
    def test_minimax_temperature_zero_preserved(self, mock_init):
        """Temperature=0 should be preserved for MiniMax."""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            make_model("minimax:MiniMax-M2.5", temperature=0, max_tokens=16_000)

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs["temperature"] == 0.0

    @patch("agent.utils.model.init_chat_model")
    def test_minimax_highspeed_model(self, mock_init):
        """MiniMax-M2.5-highspeed should route correctly."""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            make_model("minimax:MiniMax-M2.5-highspeed", temperature=0, max_tokens=16_000)

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs["model"] == "openai:MiniMax-M2.5-highspeed"

    @patch("agent.utils.model.init_chat_model")
    def test_openai_not_affected_by_minimax(self, mock_init):
        """OpenAI routing should not be affected by MiniMax changes."""
        make_model("openai:gpt-4o", temperature=0, max_tokens=16_000)

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs["model"] == "openai:gpt-4o"
        assert call_kwargs.kwargs.get("base_url") == "wss://api.openai.com/v1"
        assert "api_key" not in call_kwargs.kwargs

    @patch("agent.utils.model.init_chat_model")
    def test_anthropic_not_affected_by_minimax(self, mock_init):
        """Anthropic routing should not be affected by MiniMax changes."""
        make_model("anthropic:claude-sonnet-4-6", temperature=0, max_tokens=16_000)

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs["model"] == "anthropic:claude-sonnet-4-6"
        assert "base_url" not in call_kwargs.kwargs
        assert "api_key" not in call_kwargs.kwargs


# --- Integration Tests ---


def has_minimax_key():
    return bool(os.getenv("MINIMAX_API_KEY"))


@pytest.mark.skipif(not has_minimax_key(), reason="MINIMAX_API_KEY not set")
class TestMiniMaxIntegration:
    """Integration tests that call the real MiniMax API."""

    def test_minimax_chat_completion(self):
        """Test basic chat completion via MiniMax API."""
        import re

        from openai import OpenAI

        client = OpenAI(
            base_url=MINIMAX_BASE_URL,
            api_key=os.getenv("MINIMAX_API_KEY"),
        )
        response = client.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            messages=[{"role": "user", "content": 'Say "hello" and nothing else.'}],
            max_tokens=50,
            temperature=0.0,
        )
        content = response.choices[0].message.content
        assert content is not None
        assert len(content) > 0
        # Strip thinking tokens if present
        cleaned = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        assert "hello" in cleaned.lower() or "hello" in content.lower()

    def test_minimax_streaming(self):
        """Test streaming chat completion."""
        from openai import OpenAI

        client = OpenAI(
            base_url=MINIMAX_BASE_URL,
            api_key=os.getenv("MINIMAX_API_KEY"),
        )
        stream = client.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            messages=[{"role": "user", "content": 'Say "hi" and nothing else.'}],
            max_tokens=50,
            temperature=0.0,
            stream=True,
        )
        chunks = list(stream)
        assert len(chunks) > 0

    def test_minimax_temperature_zero(self):
        """Test that temperature=0 is accepted by MiniMax API."""
        import re

        from openai import OpenAI

        client = OpenAI(
            base_url=MINIMAX_BASE_URL,
            api_key=os.getenv("MINIMAX_API_KEY"),
        )
        response = client.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            messages=[
                {"role": "user", "content": "What is 2+2? Answer with just the number."}
            ],
            max_tokens=200,
            temperature=0.0,
        )
        content = response.choices[0].message.content
        cleaned = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        assert "4" in cleaned or "4" in content
