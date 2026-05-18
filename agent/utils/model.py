from typing import Literal, TypedDict, Unpack

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

# Anthropic SDK default is 2; a 529 burst can outlive that. Bump to give the
# primary provider a fair chance before the fallback middleware kicks in.
DEFAULT_MAX_RETRIES = 6


OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
AnthropicThinkingType = Literal["adaptive"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort


class AnthropicThinking(TypedDict, total=False):
    type: AnthropicThinkingType


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    thinking: AnthropicThinking | None
    effort: AnthropicEffort | None
    temperature: float | None
    max_retries: int | None


def make_model(model_id: str, **kwargs: Unpack[ModelKwargs]):
    model_kwargs: dict[str, object] = kwargs.copy()
    model_kwargs.setdefault("max_retries", DEFAULT_MAX_RETRIES)

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True

    return init_chat_model(model=model_id, **model_kwargs)


def fallback_model_id_for(primary_model_id: str) -> str | None:
    """Return the cross-provider fallback model id for a given primary, if any.

    Anthropic primaries fall back to OpenAI and vice versa. Returns ``None``
    when the provider has no configured cross-provider fallback (e.g. local
    or self-hosted providers we don't want to silently route off-host).
    """
    if primary_model_id.startswith("anthropic:"):
        return "openai:gpt-5.5"
    if primary_model_id.startswith("openai:"):
        return "anthropic:claude-opus-4-5"
    return None
