"""Supported models and reasoning efforts surfaced in the profile editor."""

from typing import NotRequired, TypedDict

from openswe.config import ENV


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

FABLE_MODEL_IDS: frozenset[str] = frozenset(
    m["id"] for m in SUPPORTED_MODELS if m["id"].startswith("anthropic:claude-fable")
)
NON_DEFAULT_MODEL_IDS: frozenset[str] = frozenset(
    m["id"] for m in SUPPORTED_MODELS if not m.get("can_be_default", True)
)


def available_requested_models(*, fable_enabled: bool) -> dict[str, ModelOption]:
    return {
        model["id"]: model
        for model in SUPPORTED_MODELS
        if fable_enabled or model["id"] not in FABLE_MODEL_IDS
    }


DEPRECATED_MODEL_IDS: frozenset[str] = frozenset(
    {
        "anthropic:claude-opus-4-8",
        "anthropic:claude-sonnet-5",
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-fable-5",
        "openai:gpt-5.5",
        "openai:gpt-5.6-sol",
        "openai:gpt-5.6-terra",
        "openai:gpt-5.6-luna",
        "openai:gpt-6-sol",
        "google_genai:gemini-3.5-flash",
        "google_genai:gemini-3.6-flash",
        "google_genai:gemini-3.7-flash",
        "fireworks:accounts/fireworks/models/kimi-k2p7-code",
        "fireworks:accounts/fireworks/models/kimi-k3-code",
        "fireworks:accounts/fireworks/models/glm-5p2",
        "fireworks:accounts/fireworks/models/glm-5p3",
        "fireworks:accounts/fireworks/models/glm-5p3-flash",
        "fireworks:accounts/fireworks/models/deepseek-v4-pro",
    }
)

DEPRECATED_MODEL_REPLACEMENTS: dict[str, str] = dict.fromkeys(DEPRECATED_MODEL_IDS, "")


def fable_disabled_fallback(effort: object = None) -> tuple[str, str]:
    """Newest supported non-Fable Anthropic model (keeps the Claude family),
    else the global default. Substitutes a Fable selection when Fable is
    disabled workspace-wide, preserving ``effort`` when the fallback supports it."""
    for m in SUPPORTED_MODELS:
        if m["id"].startswith("anthropic:") and m["id"] not in FABLE_MODEL_IDS:
            return m["id"], _fallback_effort_for(m, effort) or m["default_effort"]
    return default_model_pair()


def gate_fable_model(
    model_id: str, effort: str | None, *, fable_enabled: bool
) -> tuple[str, str | None]:
    """ZDR guard: if Fable is disabled but a Fable id was resolved, swap in a
    safe non-Fable model. Non-Fable selections pass through unchanged. Applied
    at every model-construction entrypoint so a disabled Fable model can never
    reach ``make_model``, no matter which layer selected it."""
    if not fable_enabled and isinstance(model_id, str) and model_id in FABLE_MODEL_IDS:
        return fable_disabled_fallback(effort)
    return model_id, effort


DEFAULT_MODEL_ID: str = (
    "anthropic:claude-opus-5-5"
    if ENV.ANTHROPIC_API_KEY.optional() and not ENV.OPENAI_API_KEY.optional()
    else "openai:gpt-6.1-sol"
)
DEFAULT_MODEL_EFFORT: str = "medium"


def model_supports_effort(model_id: str, effort: str) -> bool:
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return effort in m["efforts"]
    return False


def model_supports_images(model_id: str) -> bool:
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return m["supports_images"]
    return False


def model_context_window(model_id: str) -> int | None:
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return m["context_window"]
    return None


def _provider_of(model_id: str) -> str | None:
    provider, _, rest = model_id.partition(":")
    return provider if rest else None


def _claude_family_of(model_id: str) -> str | None:
    provider, _, name = model_id.partition(":")
    if provider != "anthropic" or not name.startswith("claude-"):
        return None
    parts = name.split("-")
    if len(parts) < 2:
        return None
    return "-".join(parts[:2])


def _fallback_effort_for(model: ModelOption, effort: object) -> str | None:
    if not isinstance(effort, str):
        return None
    if effort in model["efforts"]:
        return effort
    if (
        model["id"].startswith("google_genai:")
        and effort == "none"
        and "minimal" in model["efforts"]
    ):
        return "minimal"
    return None


def normalize_model_choice(model_id: object, effort: object) -> tuple[str | None, str | None]:
    if (
        not isinstance(model_id, str)
        or model_id not in SUPPORTED_MODEL_IDS
        or not isinstance(effort, str)
        or not model_supports_effort(model_id, effort)
    ):
        return None, None
    return model_id, effort


def provider_fallback_pair(model_id: object, effort: object = None) -> tuple[str, str] | None:
    """Newest supported ``(model_id, effort)`` for the same provider/family.

    Keeps a stored selection on its original provider when its exact id has
    dropped out of the supported set (e.g. an Opus minor-version bump), preferring
    the same Claude family when available instead of falling through to the
    cross-provider global default. Preserves ``effort`` when the fallback model
    supports it, otherwise uses that model's default effort. Returns ``None`` when
    no supported model shares the provider.

    Explicitly deprecated ids inherit the workspace default instead.
    """
    if not isinstance(model_id, str) or model_id in DEPRECATED_MODEL_IDS:
        return None
    provider = _provider_of(model_id)
    if provider is None:
        return None
    family = _claude_family_of(model_id)
    if family is not None:
        for m in SUPPORTED_MODELS:
            if _provider_of(m["id"]) == provider and _claude_family_of(m["id"]) == family:
                return m["id"], _fallback_effort_for(m, effort) or m["default_effort"]
    for m in SUPPORTED_MODELS:
        if _provider_of(m["id"]) == provider:
            return m["id"], _fallback_effort_for(m, effort) or m["default_effort"]
    return None


def default_model_pair() -> tuple[str, str]:
    """Deployment fallback used when neither the workspace nor the instance sets a default."""
    model_id = ENV.LLM_MODEL_ID.get(DEFAULT_MODEL_ID)
    effort = ENV.LLM_REASONING_EFFORT.get()
    for model in SUPPORTED_MODELS:
        if model["id"] == model_id and model.get("can_be_default", True):
            effort = (
                effort
                or _fallback_effort_for(model, DEFAULT_MODEL_EFFORT)
                or model["default_effort"]
            )
            if effort not in model["efforts"]:
                raise ValueError(f"Unsupported LLM_REASONING_EFFORT {effort!r} for {model_id!r}")
            return model_id, effort
    raise ValueError(f"Unsupported default LLM_MODEL_ID: {model_id!r}")


def default_vision_model_pair() -> tuple[str, str]:
    """Default OpenAI/Anthropic model pair to use when image input is required."""
    if (
        DEFAULT_MODEL_ID in SUPPORTED_MODEL_IDS
        and model_supports_images(DEFAULT_MODEL_ID)
        and model_supports_effort(DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT)
        and DEFAULT_MODEL_ID.startswith(("openai:", "anthropic:"))
    ):
        return DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT
    for model in SUPPORTED_MODELS:
        if model["id"].startswith(("openai:", "anthropic:")) and model["supports_images"]:
            return model["id"], model["default_effort"]
    return default_model_pair()
