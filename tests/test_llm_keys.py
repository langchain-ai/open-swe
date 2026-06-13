"""Tests for ``agent.utils.llm_keys.validate_llm_api_keys``.

Covers:
- Skips cleanly when bypass env var is set.
- Skips cleanly for unrecognised provider prefixes.
- Rejects empty / whitespace / known-placeholder keys with a clear error.
- Rejects keys that match the ``sk-...`` short-pattern placeholder.
- Returns the validated provider name on success.
- Calls the live verification helper with the right client + arguments.
- Forwards underlying SDK errors as LLMKeyValidationError with guidance.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.utils.llm_keys import (
    LLMKeyValidationError,
    _looks_like_placeholder,
    _provider_prefix,
    validate_llm_api_keys,
)


class TestProviderPrefix:
    def test_openai_prefix(self) -> None:
        assert _provider_prefix("openai:gpt-5.5") == "openai"

    def test_anthropic_prefix(self) -> None:
        assert _provider_prefix("anthropic:claude-opus-4-5") == "anthropic"

    def test_google_prefix(self) -> None:
        assert _provider_prefix("google:gemini-1.5-pro") == "google"

    def test_no_prefix(self) -> None:
        assert _provider_prefix("gpt-5.5") is None

    def test_unknown_prefix(self) -> None:
        assert _provider_prefix("custom:my-model") is None

    def test_empty_prefix(self) -> None:
        assert _provider_prefix(":gpt-5.5") is None


class TestPlaceholderDetection:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "your-key-here",
            "your_key_here",
            "<YOUR_KEY>",
            "<your_key>",
            "sk-xxx",
            "sk-",
            "sk-123",  # too short after the prefix
            "sk-ant-x",  # too short after the anthropic prefix
            "changeme",
            "***",
        ],
    )
    def test_rejects_placeholder(self, value: str) -> None:
        assert _looks_like_placeholder(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "sk-1234567890abcdef1234567890abcdef",  # plausible OpenAI key length
            "sk-ant-1234567890abcdef1234567890abcdef1234567890abcdef",  # plausible Anthropic key
            "AIzaSyDummyButLongEnoughToNotBeAPlaceholder",
            "just-a-real-looking-key",
        ],
    )
    def test_accepts_real_looking_key(self, value: str) -> None:
        assert _looks_like_placeholder(value) is False


class TestSkipBypass:
    def test_returns_empty_when_skip_env_set(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPEN_SWE_SKIP_LLM_KEY_VALIDATION": "1"},
            clear=True,
        ):
            assert validate_llm_api_keys() == ""


class TestUnsupportedProvider:
    def test_skips_unknown_provider(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_MODEL_ID": "custom:my-model"},
            clear=True,
        ):
            # No env var for 'custom', but should not raise.
            assert validate_llm_api_keys(perform_live_check=False) == ""

    def test_skips_when_no_prefix(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_MODEL_ID": "gpt-5.5"},
            clear=True,
        ):
            assert validate_llm_api_keys(perform_live_check=False) == ""


class TestOpenAIValidation:
    def test_missing_key_raises_with_guidance(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(LLMKeyValidationError) as exc_info:
                validate_llm_api_keys(perform_live_check=False)
        msg = str(exc_info.value)
        assert "OPENAI_API_KEY" in msg
        assert "platform.openai.com" in msg
        assert "Detected reason" in msg

    def test_empty_key_raises(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=True):
            with pytest.raises(LLMKeyValidationError, match="OPENAI_API_KEY"):
                validate_llm_api_keys(perform_live_check=False)

    def test_placeholder_key_raises(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "your-key-here"},
            clear=True,
        ):
            with pytest.raises(LLMKeyValidationError, match="placeholder"):
                validate_llm_api_keys(perform_live_check=False)

    def test_sk_xxx_short_key_raises(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-xxx"},
            clear=True,
        ):
            with pytest.raises(LLMKeyValidationError):
                validate_llm_api_keys(perform_live_check=False)

    def test_live_check_calls_models_list(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-" + "x" * 40},
            clear=True,
        ):
            fake_client = MagicMock()
            with patch("agent.utils.llm_keys.OpenAI", return_value=fake_client) as openai_cls:
                provider = validate_llm_api_keys(perform_live_check=True)
            assert provider == "openai"
            openai_cls.assert_called_once_with(api_key="sk-" + "x" * 40)
            fake_client.models.list.assert_called_once_with()

    def test_live_check_forwards_sdk_error(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-" + "x" * 40},
            clear=True,
        ):
            fake_client = MagicMock()
            fake_client.models.list.side_effect = RuntimeError("401 Unauthorized")
            with patch("agent.utils.llm_keys.OpenAI", return_value=fake_client):
                with pytest.raises(LLMKeyValidationError) as exc_info:
                    validate_llm_api_keys(perform_live_check=True)
            msg = str(exc_info.value)
            assert "OPENAI_API_KEY" in msg
            assert "401 Unauthorized" in msg

    def test_missing_openai_package_raises(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-" + "x" * 40},
            clear=True,
        ):
            with patch.dict("sys.modules", {"openai": None}):
                with pytest.raises(LLMKeyValidationError, match="openai package"):
                    validate_llm_api_keys(perform_live_check=True)

    def test_no_live_check_skips_sdk_call(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-" + "x" * 40},
            clear=True,
        ):
            with patch("agent.utils.llm_keys.OpenAI") as openai_cls:
                provider = validate_llm_api_keys(perform_live_check=False)
            assert provider == "openai"
            openai_cls.assert_not_called()


class TestAnthropicValidation:
    def test_missing_anthropic_key_raises(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_MODEL_ID": "anthropic:claude-opus-4-5"},
            clear=True,
        ):
            with pytest.raises(LLMKeyValidationError) as exc_info:
                validate_llm_api_keys(perform_live_check=False)
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
        assert "console.anthropic.com" in str(exc_info.value)

    def test_live_check_calls_messages_create_with_max_tokens_1(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL_ID": "anthropic:claude-opus-4-5",
                "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40,
            },
            clear=True,
        ):
            fake_client = MagicMock()
            with patch(
                "agent.utils.llm_keys.anthropic.Anthropic",
                return_value=fake_client,
            ) as anthropic_cls:
                provider = validate_llm_api_keys(perform_live_check=True)
            assert provider == "anthropic"
            anthropic_cls.assert_called_once_with(
                api_key="sk-ant-" + "x" * 40,
            )
            fake_client.messages.create.assert_called_once()
            kwargs = fake_client.messages.create.call_args.kwargs
            assert kwargs["max_tokens"] == 1
            assert kwargs["messages"] == [{"role": "user", "content": "ping"}]

    def test_live_check_forwards_anthropic_sdk_error(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL_ID": "anthropic:claude-opus-4-5",
                "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40,
            },
            clear=True,
        ):
            fake_client = MagicMock()
            fake_client.messages.create.side_effect = RuntimeError("invalid api key")
            with patch(
                "agent.utils.llm_keys.anthropic.Anthropic",
                return_value=fake_client,
            ):
                with pytest.raises(LLMKeyValidationError) as exc_info:
                    validate_llm_api_keys(perform_live_check=True)
            assert "ANTHROPIC_API_KEY" in str(exc_info.value)
            assert "invalid api key" in str(exc_info.value)


class TestGoogleValidation:
    def test_missing_google_key_raises(self) -> None:
        with patch.dict(
            "os.environ",
            {"LLM_MODEL_ID": "google:gemini-1.5-pro"},
            clear=True,
        ):
            with pytest.raises(LLMKeyValidationError) as exc_info:
                validate_llm_api_keys(perform_live_check=False)
        assert "GOOGLE_API_KEY" in str(exc_info.value)
        assert "aistudio.google.com" in str(exc_info.value)

    def test_live_check_calls_generate_content_with_max_output_tokens_1(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL_ID": "google:gemini-1.5-pro",
                "GOOGLE_API_KEY": "AIza" + "x" * 35,
            },
            clear=True,
        ):
            fake_module = MagicMock()
            fake_model = MagicMock()
            fake_module.GenerativeModel.return_value = fake_model
            with patch.dict(
                "sys.modules",
                {"google.generativeai": fake_module},
            ):
                provider = validate_llm_api_keys(perform_live_check=True)
            assert provider == "google"
            fake_module.configure.assert_called_once_with(
                api_key="AIza" + "x" * 35,
            )
            fake_model.generate_content.assert_called_once()
            kwargs = fake_model.generate_content.call_args.kwargs
            assert kwargs["generation_config"]["max_output_tokens"] == 1


class TestModelOverride:
    def test_explicit_model_id_overrides_env(self) -> None:
        # Env says openai; override says anthropic; expect anthropic key path.
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL_ID": "openai:gpt-5.5",
                "OPENAI_API_KEY": "sk-" + "x" * 40,
            },
            clear=True,
        ):
            with pytest.raises(LLMKeyValidationError, match="ANTHROPIC_API_KEY"):
                validate_llm_api_keys(
                    perform_live_check=False,
                    model_id="anthropic:claude-opus-4-5",
                )
