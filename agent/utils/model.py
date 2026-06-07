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


_ANTHROPIC_EFFORTS: set[AnthropicEffort] = {"low", "medium", "high", "xhigh", "max"}


def make_model(model_id: str, **kwargs: Unpack[ModelKwargs]):
    import os

    from dotenv import load_dotenv

    # Robustly find and load the .env file in project root
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    while cur_dir and not os.path.exists(os.path.join(cur_dir, ".env")):
        parent = os.path.dirname(cur_dir)
        if parent == cur_dir:
            break
        cur_dir = parent
    env_path = os.path.join(cur_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
    else:
        load_dotenv(override=True)

    # Dynamic mapping of DeepSeek credentials/endpoints
    if os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
    if os.environ.get("DEEPSEEK_BASE_URL") and not os.environ.get("OPENAI_API_BASE"):
        os.environ["OPENAI_API_BASE"] = os.environ["DEEPSEEK_BASE_URL"]

    # Rewrite model ID to DeepSeek model name if we are routing to DeepSeek
    openai_base = os.environ.get("OPENAI_API_BASE", "")
    is_deepseek = "deepseek" in openai_base.lower() or bool(os.environ.get("DEEPSEEK_MODEL"))

    if is_deepseek and model_id.startswith("openai:"):
        custom_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        model_id = f"openai:{custom_model}"

    model_kwargs: dict[str, object] = kwargs.copy()
    model_kwargs.setdefault("max_retries", DEFAULT_MAX_RETRIES)

    # DeepSeek doesn't support o1-style reasoning effort, pop it to avoid TypeError in openai client
    if is_deepseek:
        model_kwargs.pop("reasoning", None)
        model_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    if model_id.startswith("openai:"):
        # Only override with OpenAI responses websocket base if a custom base is not explicitly set
        if not os.environ.get("OPENAI_API_BASE"):
            model_kwargs["base_url"] = OPENAI_RESPONSES_WS_BASE_URL
            model_kwargs["use_responses_api"] = True
        else:
            model_kwargs["use_responses_api"] = False

    return init_chat_model(model=model_id, **model_kwargs)


def fallback_model_id_for(primary_model_id: str) -> str | None:
    """Return the cross-provider fallback model id for a given primary, if any.

    Anthropic primaries fall back to OpenAI and vice versa. Returns ``None``
    when the provider has no configured cross-provider fallback (e.g. Google,
    local, or self-hosted providers we don't want to silently route off-host).
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


def google_thinking_level_for(profile_effort: str | None) -> GoogleThinkingLevel | None:
    """Map profile effort to Gemini 3+ ``thinking_level``."""
    if profile_effort == "none":
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
    return kwargs
