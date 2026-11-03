"""The models Open SWE offers, and the chat model each side builds for one.

The backend picks a run's models and their settings; the runtime builds and calls
them. Both build them here, so a model the runtime calls is the one the backend
picked: the same provider settings, gateway routing and context window, which
decides when the conversation is summarized.

Credentials and routing come from the environment of the process that builds the
model, never from what the other side sends.
"""

import logging
import os
from collections.abc import Mapping
from typing import Any, Final, Literal, NotRequired, TypedDict, Unpack, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


class ModelOption(TypedDict):
    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool
    context_window: int
    can_be_default: NotRequired[bool]


SUPPORTED_MODELS: list[ModelOption] = [
    {
        "id": "anthropic:claude-opus-5-5",
        "label": "Opus 5.5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
        "context_window": 1_000_000,
    },
    {
        "id": "anthropic:claude-sonnet-5-5",
        "label": "Sonnet 5.5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
        "context_window": 1_000_000,
    },
    {
        "id": "anthropic:claude-haiku-5-5",
        "label": "Haiku 5.5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "medium",
        "supports_images": True,
        "context_window": 1_000_000,
    },
    {
        "id": "anthropic:claude-fable-5-1",
        "label": "Fable 5.1",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
        "context_window": 1_000_000,
        "can_be_default": False,
    },
    {
        "id": "openai:gpt-6-astra",
        "label": "GPT-6 Astra",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "low",
        "supports_images": True,
        # Codex serves OpenAI models with a smaller window than the public API.
        "context_window": 272_000,
    },
    {
        "id": "openai:gpt-6.1-sol",
        "label": "GPT-6.1 Sol",
        "efforts": ["none", "low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
        "supports_images": True,
        "context_window": 272_000,
    },
    {
        "id": "openai:gpt-6.1-terra",
        "label": "GPT-6.1 Terra",
        "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_effort": "medium",
        "supports_images": True,
        "context_window": 272_000,
    },
    {
        "id": "openai:gpt-6-luna",
        "label": "GPT-6 Luna",
        "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_effort": "xhigh",
        "supports_images": True,
        "context_window": 272_000,
    },
    {
        "id": "google_genai:gemini-4-argon",
        "label": "Gemini 4 Argon",
        "efforts": ["low", "medium", "high"],
        "default_effort": "medium",
        "supports_images": True,
        "context_window": 1_048_576,
    },
    {
        "id": "google_genai:gemini-3.8-flash",
        "label": "Gemini 3.8 Flash",
        "efforts": ["minimal", "low", "medium", "high"],
        "default_effort": "medium",
        "supports_images": True,
        "context_window": 1_048_576,
    },
    {
        "id": "fireworks:accounts/fireworks/models/kimi-k3",
        "label": "Kimi K3",
        # K3 always reasons and only accepts low/high/max — "medium" is rejected.
        "efforts": ["low", "high", "max"],
        "default_effort": "high",
        "supports_images": False,
        "context_window": 1_048_576,
    },
]

SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in SUPPORTED_MODELS)


def _option(model_id: str) -> ModelOption | None:
    return next((m for m in SUPPORTED_MODELS if m["id"] == model_id), None)


def model_supports_effort(model_id: str, effort: str) -> bool:
    option = _option(model_id)
    return option is not None and effort in option["efforts"]


def model_supports_images(model_id: str) -> bool:
    option = _option(model_id)
    return option is not None and option["supports_images"]


def model_context_window(model_id: str) -> int | None:
    option = _option(model_id)
    return None if option is None else option["context_window"]


# --- Provider settings ------------------------------------------------------------------------

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


DEFAULT_LLM_REASONING: OpenAIReasoning = {"effort": "medium", "summary": "auto"}


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


def fallback_model_id_for(primary_model_id: str) -> str | None:
    """Return the cross-provider fallback model id for a given primary, if any.

    Anthropic primaries fall back to OpenAI and vice versa. Returns ``None``
    when the provider has no configured cross-provider fallback (e.g. Google,
    local, or self-hosted providers we don't want to silently route off-host).
    """
    if primary_model_id.startswith("anthropic:"):
        return "openai:gpt-6.1-sol"
    if primary_model_id.startswith("openai:"):
        return "anthropic:claude-opus-5-5"
    return None


def model_request_defaults(model_id: str) -> ModelKwargs:
    if model_id == "google_genai:gemini-4-argon":
        return {"timeout": 900.0, "temperature": None}
    return {}


def supports_google_thinking_level(model_id: str) -> bool:
    return model_id.startswith("google_genai:gemini-3") or model_id == "google_genai:gemini-4-argon"


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
    ``SUPPORTED_MODELS`` gate which values can actually reach this function.
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
    kwargs = model_request_defaults(model_id)
    kwargs["max_tokens"] = max_tokens
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
    elif supports_google_thinking_level(model_id):
        thinking_level = google_thinking_level_for(profile_effort)
        if thinking_level is not None:
            kwargs["thinking_level"] = thinking_level
    elif model_id.startswith("fireworks:"):
        effort = fireworks_reasoning_effort_for(profile_effort)
        if effort is not None:
            kwargs["model_kwargs"] = {"reasoning_effort": effort}
    elif model_id.startswith("baseten:") and profile_effort in ("low", "high", "max"):
        kwargs["reasoning_effort"] = profile_effort
    return kwargs


# --- LangSmith LLM Gateway --------------------------------------------------------------------
#
# The LLM Gateway (https://docs.langchain.com/langsmith/llm-gateway) proxies
# provider calls through LangSmith: the client authenticates with a LangSmith API
# key and the gateway resolves the real provider key from workspace Provider
# Secrets, enforcing spend/PII/secrets policies and tracing every call. Routing is
# opt-in via ``LANGSMITH_GATEWAY_ENABLED`` (deployment default) or the
# ``gateway_enabled`` workspace setting, and is applied centrally in ``make_model``.

DEFAULT_GATEWAY_BASE_URL: Final = "https://gateway.smith.langchain.com"

# Provider prefix -> base-URL suffix appended to the gateway host. Each suffix
# matches the SDK's own path handling: the OpenAI SDK appends
# ``/chat/completions`` to a ``/v1`` base, Fireworks appends
# ``/v1/chat/completions`` to a bare provider host, Anthropic appends
# ``/v1/messages`` to a bare host, and google-genai appends
# ``/<api_version>/models/...`` to a bare host. Vertex (``google_vertexai``, which
# uses service-account auth rather than a bearer key) and any other provider are
# not routed and call the provider directly.
_GATEWAY_PROVIDER_PATHS: Final[Mapping[str, str]] = {
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
    "baseten": "/baseten/v1",
    "fireworks": "/fireworks",
    "google_genai": "/gemini",
}


def _env(*names: str) -> str | None:
    """The first of ``names`` set to something other than whitespace."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def gateway_api_key() -> str | None:
    """LangSmith API key used to authenticate gateway calls.

    Prefer a gateway-specific key, then the standard LangSmith key. The key
    LangGraph Platform injects as ``LANGSMITH_API_KEY`` can lack the
    ``gateway:invoke`` permission, which is what ``LANGSMITH_GATEWAY_API_KEY`` is for.
    """
    return _env("LANGSMITH_GATEWAY_API_KEY") or _env("LANGSMITH_API_KEY")


def gateway_base_url() -> str:
    """Gateway host, overridable via ``LANGSMITH_GATEWAY_BASE_URL`` (regional/self-hosted)."""
    return (_env("LANGSMITH_GATEWAY_BASE_URL") or DEFAULT_GATEWAY_BASE_URL).rstrip("/")


def gateway_env_default() -> bool:
    """Deployment-level default for gateway routing.

    ``LANGSMITH_GATEWAY_ENABLED`` decides when set. Otherwise a dedicated
    ``LANGSMITH_GATEWAY_API_KEY`` is the signal: that key exists for nothing
    else, so configuring it means routing through the gateway.
    """
    explicit = _env("LANGSMITH_GATEWAY_ENABLED")
    if explicit is not None:
        return _env_bool(explicit)
    return bool(_env("LANGSMITH_GATEWAY_API_KEY"))


def gateway_openai_use_responses() -> bool:
    """Whether gateway-routed OpenAI keeps the Responses API.

    Defaults to ``True`` because OpenAI reasoning models with tool calls reject
    ``reasoning_effort`` on Chat Completions. Set
    ``LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES=false`` only for deployments that
    need to force Chat Completions through the gateway.
    """
    raw = _env("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES")
    if raw is None:
        return True
    return _env_bool(raw)


def resolve_gateway_enabled(team_value: bool | None) -> bool:
    """Combine the workspace-settings toggle with the env default.

    A workspace value of ``True``/``False`` is authoritative; ``None`` inherits the
    ``LANGSMITH_GATEWAY_ENABLED`` deployment default.
    """
    if team_value is None:
        return gateway_env_default()
    return team_value


def gateway_overrides(model_id: str) -> dict[str, object] | None:
    """``init_chat_model`` kwargs that route ``model_id`` through the gateway.

    Returns ``None`` (so the caller keeps talking to the provider directly) when
    the provider isn't routable through the gateway or no LangSmith API key is
    available — both cases are logged rather than raised, so a run never fails
    just because gateway routing couldn't be applied.
    """
    provider = model_id.split(":", 1)[0]
    path = _GATEWAY_PROVIDER_PATHS.get(provider)
    if path is None:
        logger.warning(
            "LangSmith gateway enabled but provider %r is not routed; calling it directly",
            provider,
        )
        return None
    api_key = gateway_api_key()
    if not api_key:
        logger.warning(
            "LangSmith gateway enabled but no LANGSMITH_GATEWAY_API_KEY or "
            "LANGSMITH_API_KEY is set; "
            "calling the provider directly"
        )
        return None
    overrides: dict[str, object] = {
        "base_url": f"{gateway_base_url()}{path}",
        "api_key": api_key,
    }
    if provider == "openai":
        # Use HTTPS Responses through the gateway by default; tool-calling OpenAI
        # reasoning models reject reasoning_effort on Chat Completions.
        overrides["use_responses_api"] = gateway_openai_use_responses()
    return overrides


# --- Building the chat model ------------------------------------------------------------------

OPENAI_RESPONSES_WS_BASE_URL: Final = "wss://api.openai.com/v1"
BASETEN_BASE_URL: Final = "https://inference.baseten.co/v1"

# Anthropic SDK default is 2; a 529 burst can outlive that. Bump to give the
# primary provider a fair chance before the fallback kicks in.
DEFAULT_MAX_RETRIES: Final = 6

# Per-request deadline. Without one a stalled provider connection can park a run
# for as long as the socket stays half-open (observed: a single call wedged for an
# hour). Every provider we ship accepts ``timeout``; keep it generous enough for
# max-effort reasoning on a long context, and let ``max_retries`` above turn a
# stall into a retry instead of a dead run.
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 600.0
_TIMEOUT_PROVIDER_PREFIXES: Final = (
    "openai:",
    "anthropic:",
    "baseten:",
    "google_genai:",
    "fireworks:",
)


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
        model_kwargs["include"] = [*include, "reasoning.encrypted_content"]


def _codex_profile(model_id: str) -> dict[str, object] | None:
    """OpenAI's models.dev profile with the smaller window Codex serves them at."""
    context_window = model_context_window(model_id)
    if not model_id.startswith("openai:") or context_window is None:
        return None
    # The bundled profile has no public accessor; init_chat_model imports this module next anyway.
    from langchain_openai.chat_models.base import _get_default_model_profile  # noqa: PLC2701

    profile: dict[str, object] = dict(_get_default_model_profile(model_id.partition(":")[2]))
    profile["max_input_tokens"] = context_window
    return profile


def model_init_kwargs(
    model_id: str, kwargs: Mapping[str, object], *, use_gateway: bool | None = None
) -> tuple[str, dict[str, object]]:
    """The model id and keyword arguments ``init_chat_model`` builds ``model_id`` from.

    ``use_gateway`` resolves the deployment default (``LANGSMITH_GATEWAY_ENABLED``)
    when ``None``; callers that read the workspace's setting pass its value. When on,
    gateway ``base_url``/``api_key``/``use_responses_api`` override the direct
    provider defaults below.
    """
    model_kwargs: dict[str, object] = dict(model_request_defaults(model_id))
    model_kwargs.update(kwargs)
    model_kwargs.setdefault("max_retries", DEFAULT_MAX_RETRIES)
    if model_id.startswith(_TIMEOUT_PROVIDER_PREFIXES):
        model_kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT_SECONDS)

    if model_id.startswith("openai:"):
        model_kwargs["base_url"] = (
            _env("OPENAI_BASE_URL", "OPENAI_API_BASE") or OPENAI_RESPONSES_WS_BASE_URL
        )
        model_kwargs["use_responses_api"] = True

    enabled = gateway_env_default() if use_gateway is None else use_gateway
    gateway_applied = False
    if enabled:
        overrides = gateway_overrides(model_id)
        if overrides is not None:
            model_kwargs.update(overrides)
            gateway_applied = True

    if model_id.startswith("openai:"):
        _configure_openai_responses_kwargs(model_kwargs)
        _coerce_openai_chat_completions_kwargs(model_kwargs)

    init_model_id = model_id
    if model_id.startswith("baseten:"):
        init_model_id = model_id.split(":", 1)[1]
        model_kwargs["model_provider"] = "openai"
        if not gateway_applied:
            api_key = _env("BASETEN_API_KEY")
            if not api_key:
                raise ValueError("BASETEN_API_KEY is required when Gateway routing is disabled")
            model_kwargs["base_url"] = BASETEN_BASE_URL
            model_kwargs["api_key"] = api_key

    codex_profile = _codex_profile(model_id)
    if codex_profile is not None:
        model_kwargs["profile"] = codex_profile
    return init_model_id, model_kwargs


def build_chat_model(
    model_id: str, kwargs: Mapping[str, object], *, use_gateway: bool | None = None
) -> BaseChatModel:
    """The chat model for ``model_id`` with ``kwargs``, as ``model_init_kwargs`` sets it up."""
    init_model_id, model_kwargs = model_init_kwargs(model_id, kwargs, use_gateway=use_gateway)
    return init_chat_model(model=init_model_id, **cast(dict[str, Any], model_kwargs))


def make_model(
    model_id: str, *, use_gateway: bool | None = None, **kwargs: Unpack[ModelKwargs]
) -> BaseChatModel:
    """Build a chat model, optionally routed through the LangSmith LLM Gateway."""
    return build_chat_model(model_id, kwargs, use_gateway=use_gateway)
