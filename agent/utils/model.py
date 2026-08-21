import asyncio
from typing import Any, Literal, TypedDict, TypeVar, Unpack, cast

from langchain.chat_models import init_chat_model

from ..config import (
    default_llm_model_id,
    is_local_dev,
    langsmith_gateway_enabled_default,
    missing_provider_api_key,
    openai_base_url,
)
from ..dashboard.options import DEFAULT_MODEL_ID, model_profile_with_context_override
from .gateway import gateway_overrides

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

# Anthropic SDK default is 2; a 529 burst can outlive that. Bump to give the
# primary provider a fair chance before the fallback middleware kicks in.
DEFAULT_MAX_RETRIES = 6

# Per-request deadline. Without one a stalled provider connection can park a run
# for as long as the socket stays half-open (observed: a single call wedged for an
# hour). Every provider we ship accepts ``timeout``; keep it generous enough for
# max-effort reasoning on a long context, and let ``max_retries`` above turn a
# stall into a retry instead of a dead run.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0
_TIMEOUT_PROVIDER_PREFIXES = ("openai:", "anthropic:", "google_genai:", "fireworks:")

_MODEL_CACHE: dict[
    tuple[str, bool | None, int | None, tuple[tuple[str, str], ...], int | None], Any
] = {}


def _loop_cache_key() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


def _freeze_model_kwargs(kwargs: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, repr(value)) for key, value in kwargs.items()))


async def close_cached_models() -> None:
    models = list(_MODEL_CACHE.values())
    _MODEL_CACHE.clear()
    for model in models:
        close = getattr(model, "aclose", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result
            continue
        close = getattr(model, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result


OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
# OpenAI's Responses API only returns human-readable reasoning text when a
# summary is requested; without it, reasoning happens silently (billed in
# output tokens) and the reasoning content block arrives empty.
OpenAIReasoningSummary = Literal["auto", "concise", "detailed"]
AnthropicThinkingType = Literal["adaptive"]
AnthropicThinkingDisplay = Literal["summarized", "omitted"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
GoogleThinkingLevel = Literal["minimal", "low", "medium", "high"]
FireworksReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort
    summary: OpenAIReasoningSummary


DEFAULT_LLM_REASONING: "OpenAIReasoning" = {"effort": "medium", "summary": "auto"}


class AnthropicThinking(TypedDict, total=False):
    type: AnthropicThinkingType
    display: AnthropicThinkingDisplay


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    reasoning_effort: OpenAIReasoningEffort | None
    thinking: AnthropicThinking | None
    effort: AnthropicEffort | None
    thinking_level: GoogleThinkingLevel | None
    temperature: float | None
    max_retries: int | None
    timeout: float | None
    store: bool | None
    include: list[str] | None
    output_version: Literal["responses/v1"] | None
    model_kwargs: dict[str, object] | None


_ANTHROPIC_EFFORTS: set[AnthropicEffort] = {"low", "medium", "high", "xhigh", "max"}


def _coerce_openai_chat_completions_kwargs(model_kwargs: dict[str, object]) -> None:
    if model_kwargs.get("use_responses_api") is not False:
        return
    reasoning = model_kwargs.pop("reasoning", None)
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str):
            model_kwargs.setdefault("reasoning_effort", effort)


def _configure_openai_responses_kwargs(model_kwargs: dict[str, object]) -> None:
    if model_kwargs.get("use_responses_api") is False:
        return
    model_kwargs.setdefault("store", False)
    model_kwargs.setdefault("output_version", "responses/v1")
    include = model_kwargs.get("include")
    if include is None:
        model_kwargs["include"] = ["reasoning.encrypted_content"]
    elif isinstance(include, list) and "reasoning.encrypted_content" not in include:
        include.append("reasoning.encrypted_content")


_ChatModelT = TypeVar("_ChatModelT")

# A model reaches middleware wrapped in whatever the agent bound onto it, which
# is a handful of `RunnableBinding` layers at most.
_MAX_BOUND_DEPTH = 10


def unwrap_chat_model(model: object, kind: type[_ChatModelT]) -> _ChatModelT | None:
    """Return the ``kind`` chat model under a runnable's ``bound`` chain, if there is one.

    The walk is capped and remembers what it has seen so a binding that points
    at itself cannot spin.
    """
    current = model
    seen: set[int] = set()
    for _ in range(_MAX_BOUND_DEPTH):
        if isinstance(current, kind):
            return current
        current_id = id(current)
        if current_id in seen:
            return None
        seen.add(current_id)
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            return None
        current = bound
    return None


def make_model(model_id: str, *, use_gateway: bool | None = None, **kwargs: Unpack[ModelKwargs]):
    """Build a chat model, optionally routed through the LangSmith LLM Gateway.

    ``use_gateway`` resolves the deployment default (``LANGSMITH_GATEWAY_ENABLED``)
    when ``None``; async callers pass the team-settings-resolved value. When on,
    gateway ``base_url``/``api_key``/``use_responses_api`` override the direct
    provider defaults below (see :mod:`agent.utils.gateway`).
    """
    model_kwargs: dict[str, object] = dict(kwargs)
    model_kwargs.setdefault("max_retries", DEFAULT_MAX_RETRIES)
    if model_id.startswith(_TIMEOUT_PROVIDER_PREFIXES):
        model_kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT_SECONDS)

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = openai_base_url() or OPENAI_RESPONSES_WS_BASE_URL
        model_kwargs["use_responses_api"] = True

    enabled = langsmith_gateway_enabled_default() if use_gateway is None else use_gateway
    if enabled:
        overrides = gateway_overrides(model_id)
        if overrides is not None:
            model_kwargs.update(overrides)

    if model_id.startswith("openai:"):
        _configure_openai_responses_kwargs(model_kwargs)
        _coerce_openai_chat_completions_kwargs(model_kwargs)

    profile_override = model_profile_with_context_override(model_id)
    if profile_override is not None:
        model_kwargs["profile"] = profile_override

    max_tokens = model_kwargs.get("max_tokens")
    max_tokens_key = max_tokens if type(max_tokens) is int else None
    key = (
        model_id,
        use_gateway,
        max_tokens_key,
        _freeze_model_kwargs(model_kwargs),
        _loop_cache_key(),
    )
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    model = init_chat_model(model=model_id, **cast(dict[str, Any], model_kwargs))
    _MODEL_CACHE[key] = model
    return model


def fallback_model_id_for(primary_model_id: str) -> str | None:
    """Return the cross-provider fallback model id for a given primary, if any.

    Anthropic primaries fall back to OpenAI and vice versa. Returns ``None``
    when the provider has no configured cross-provider fallback (e.g. Google,
    local, or self-hosted providers we don't want to silently route off-host).
    """
    if primary_model_id.startswith("anthropic:"):
        return "openai:gpt-5.6-sol"
    if primary_model_id.startswith("openai:"):
        return "anthropic:claude-opus-5"
    return None


def is_gemini_3_family(model_id: str) -> bool:
    model_name = model_id.split(":", 1)[-1]
    return model_name.startswith("gemini-3")


def openai_reasoning_for(
    profile_effort: str | None,
    *,
    default_effort: OpenAIReasoningEffort | None = None,
) -> OpenAIReasoning | None:
    """Return an OpenAI reasoning kwarg from a profile effort string.

    Requests ``summary: "auto"`` for every reasoning effort so the Responses
    API emits visible reasoning text. ``effort: "none"`` disables reasoning
    entirely, so no summary is attached.
    """
    effort = profile_effort or default_effort or DEFAULT_LLM_REASONING.get("effort")
    if effort == "none":
        return {"effort": "none"}
    if effort == "low":
        return {"effort": "low", "summary": "auto"}
    if effort == "medium":
        return {"effort": "medium", "summary": "auto"}
    if effort == "high":
        return {"effort": "high", "summary": "auto"}
    if effort == "xhigh":
        return {"effort": "xhigh", "summary": "auto"}
    if effort == "max":
        return {"effort": "max", "summary": "auto"}
    return None


def anthropic_thinking_for(profile_effort: str | None) -> AnthropicThinking | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        # `display: "summarized"` makes Opus 4.7+ return the (summarized) reasoning
        # text in the response. The adaptive default is "omitted", which streams a
        # reasoning block carrying only a signature and no visible thinking — so the
        # dashboard never has any text to render.
        return {"type": "adaptive", "display": "summarized"}
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


def validate_local_dev_llm_config() -> None:
    """Validate API keys for the locally configured default model.

    Only runs in local development: it catches missing credentials for the
    default model (``LLM_MODEL_ID``/``DEFAULT_MODEL_ID``) before the first run
    fails on them. Runtime model selection may come from team, profile, or
    thread configuration and is not validated here.
    """
    if not is_local_dev():
        return
    model_id = default_llm_model_id() or DEFAULT_MODEL_ID
    missing = missing_provider_api_key(model_id)
    if missing:
        raise ValueError(f"{missing} is required for configured model {model_id}")
