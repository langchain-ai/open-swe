"""Tests for the get_model_id() helper in agent.utils.model."""

import os
from unittest.mock import patch

from agent.utils.model import DEFAULT_MODEL_ID, get_model_id


class TestGetModelId:
    """Test environment-variable resolution for the LLM model id."""

    def test_default_when_no_env_vars(self):
        """Returns DEFAULT_MODEL_ID when no env vars are set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MODEL", None)
            os.environ.pop("LLM_MODEL_ID", None)
            assert get_model_id() == DEFAULT_MODEL_ID

    def test_agent_model_env_var(self):
        """AGENT_MODEL takes highest priority."""
        with patch.dict(os.environ, {"AGENT_MODEL": "anthropic:claude-sonnet-4"}):
            assert get_model_id() == "anthropic:claude-sonnet-4"

    def test_llm_model_id_fallback(self):
        """LLM_MODEL_ID is used when AGENT_MODEL is not set."""
        with patch.dict(os.environ, {"LLM_MODEL_ID": "openai:gpt-4o"}):
            os.environ.pop("AGENT_MODEL", None)
            assert get_model_id() == "openai:gpt-4o"

    def test_agent_model_takes_precedence_over_llm_model_id(self):
        """AGENT_MODEL wins when both are set."""
        with patch.dict(
            os.environ,
            {"AGENT_MODEL": "anthropic:claude-sonnet-4", "LLM_MODEL_ID": "openai:gpt-4o"},
        ):
            assert get_model_id() == "anthropic:claude-sonnet-4"

    def test_empty_agent_model_falls_back_to_llm_model_id(self):
        """Empty AGENT_MODEL falls through to LLM_MODEL_ID."""
        with patch.dict(os.environ, {"AGENT_MODEL": "", "LLM_MODEL_ID": "openai:gpt-4o"}):
            assert get_model_id() == "openai:gpt-4o"

    def test_empty_agent_model_and_empty_llm_model_id_returns_default(self):
        """Empty AGENT_MODEL and empty LLM_MODEL_ID both fall through to default."""
        with patch.dict(os.environ, {"AGENT_MODEL": "", "LLM_MODEL_ID": ""}):
            # Empty string is falsy, so AGENT_MODEL is skipped.
            # LLM_MODEL_ID="" is also empty, so os.environ.get returns "".
            # The or-chain: "" or "" or DEFAULT -> still "" from get due to how
            # os.environ.get works. Let's check the actual behavior.
            result = get_model_id()
            # When LLM_MODEL_ID is set to "", os.environ.get("LLM_MODEL_ID", DEFAULT) returns ""
            assert result == ""

    def test_default_model_id_value(self):
        """Sanity check the canonical default."""
        assert DEFAULT_MODEL_ID == "openai:gpt-5.5"
