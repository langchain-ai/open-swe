from typing import Literal, TypedDict, Unpack

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

# Anthropic SDK default is 2; a 529 burst can outlive that. Bump to give the
# primary provider a fair chance before the fallback middleware kicks in.
DEFAULT_MAX_RETRIES = 6

DEFAULT_LLM_REASONING: "OpenAIReasoning" = {"effort": "medium"}

OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
AnthropicThinkingType = Literal["adaptive"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
GoogleThinkingLevel = Literal["minimal", "low", "medium", "high"]
FireworksReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort


class AnthropicThinking(TypedDict, total=False):
    type: AnthropicThinkingType


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    thinking: AnthropicThinking | None
    effort: AnthropicEffort | None
    thinking_level: GoogleThinkingLevel | None
    temperature: float | None
    max_retries: int | None
    model_kwargs: dict[str, object] | None


_ANTHROPIC_EFFORTS: set[AnthropicEffort] = {"low", "medium", "high", "xhigh", "max"}


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
    when the provider has no configured cross-provider fallback (e.g. Google,
    local, or self-hosted providers we don't want to silently route off-host),
    or when the fallback provider's API key is not configured.
    """
    import os

    if primary_model_id.startswith("anthropic:"):
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"):
            return "openai:gpt-5.5"
    if primary_model_id.startswith("openai:"):
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic:claude-opus-4-5"
    return None


def is_gemini_3_family(model_id: str) -> bool:
    model_name = model_id.split(":", 1)[-1]
    return model_name.startswith("gemini-3")


def openai_reasoning_for(
    profile_effort: str | None,
    *,
    default_effort: OpenAIReasoningEffort | None = None,
) -> OpenAIReasoning | None:
    """Return an OpenAI reasoning kwarg from a profile effort string."""
    effort = profile_effort or default_effort or DEFAULT_LLM_REASONING.get("effort")
    if effort == "none":
        return {"effort": "none"}
    if effort == "low":
        return {"effort": "low"}
    if effort == "medium":
        return {"effort": "medium"}
    if effort == "high":
        return {"effort": "high"}
    if effort == "xhigh":
        return {"effort": "xhigh"}
    return None


def anthropic_thinking_for(profile_effort: str | None) -> AnthropicThinking | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return {"type": "adaptive"}
    return None


def anthropic_effort_for(profile_effort: str | None) -> AnthropicEffort | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return profile_effort
    return None


def fireworks_reasoning_effort_for(profile_effort: str | None) -> FireworksReasoningEffort | None:
    """Map profile effort to a Fireworks ``reasoning_effort`` value.

    Fireworks' OpenAI-compatible API accepts ``reasoning_effort`` on its reasoning
    models. ``none`` disables reasoning; ``xhigh``/``max`` are only honored by models
    that advertise them (e.g. DeepSeek V4 Pro). The per-model ``efforts`` lists in
    ``dashboard/options.py`` gate which values can actually reach this function.
    """
    if profile_effort == "none":
        return "none"
    if profile_effort == "low":
        return "low"
    if profile_effort == "medium":
        return "medium"
    if profile_effort == "high":
        return "high"
    if profile_effort == "xhigh":
        return "xhigh"
    if profile_effort == "max":
        return "max"
    return None


def google_thinking_level_for(profile_effort: str | None) -> GoogleThinkingLevel | None:
    """Map profile effort to Gemini 3+ ``thinking_level``."""
    if profile_effort in ("minimal", "none"):
        return "minimal"
    if profile_effort == "low":
        return "low"
    if profile_effort == "medium":
        return "medium"
    if profile_effort in ("high", "xhigh", "max"):
        return "high"
    return None


def provider_model_kwargs(
    model_id: str,
    profile_effort: str | None,
    *,
    max_tokens: int,
    openai_reasoning_default: OpenAIReasoning | None = None,
) -> ModelKwargs:
    """Build provider-specific kwargs for ``make_model`` from a model id and effort."""
    kwargs: ModelKwargs = {"max_tokens": max_tokens}
    if model_id.startswith("openai:"):
        reasoning = openai_reasoning_for(profile_effort)
        if reasoning is not None:
            kwargs["reasoning"] = reasoning
        elif openai_reasoning_default is not None:
            kwargs["reasoning"] = openai_reasoning_default
    elif model_id.startswith("anthropic:"):
        thinking = anthropic_thinking_for(profile_effort)
        if thinking is not None:
            kwargs["thinking"] = thinking
        effort = anthropic_effort_for(profile_effort)
        if effort is not None:
            kwargs["effort"] = effort
    elif model_id.startswith("google_genai:") and is_gemini_3_family(model_id):
        thinking_level = google_thinking_level_for(profile_effort)
        if thinking_level is not None:
            kwargs["thinking_level"] = thinking_level
    elif model_id.startswith("fireworks:"):
        effort = fireworks_reasoning_effort_for(profile_effort)
        if effort is not None:
            kwargs["model_kwargs"] = {"reasoning_effort": effort}
    return kwargs
