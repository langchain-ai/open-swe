"""Supported models and reasoning efforts surfaced in the profile editor."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import cache, lru_cache
from importlib import import_module
from typing import NotRequired, TypedDict, cast


class ModelOption(TypedDict):
    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool
    context_window: NotRequired[int | None]


SUPPORTED_MODELS: list[ModelOption] = [
    {
        "id": "anthropic:claude-opus-5",
        "label": "Opus 5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
    },
    {
        "id": "anthropic:claude-opus-4-8",
        "label": "Opus 4.8",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
    },
    {
        "id": "anthropic:claude-sonnet-5",
        "label": "Sonnet 5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
    },
    {
        "id": "anthropic:claude-fable-5",
        "label": "Fable 5",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": True,
    },
    {
        "id": "openai:gpt-5.5",
        "label": "GPT-5.5",
        "efforts": ["none", "low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
        "supports_images": True,
    },
    {
        "id": "openai:gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "efforts": ["none", "low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
        "supports_images": True,
    },
    {
        "id": "openai:gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "efforts": ["none", "low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
        "supports_images": True,
    },
    {
        "id": "openai:gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "efforts": ["none", "low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
        "supports_images": True,
    },
    {
        "id": "google_genai:gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "efforts": ["minimal", "low", "medium", "high"],
        "default_effort": "medium",
        "supports_images": True,
    },
    {
        "id": "fireworks:accounts/fireworks/models/kimi-k2p7-code",
        "label": "Kimi K2.7",
        "efforts": ["low", "medium", "high"],
        "default_effort": "high",
        "supports_images": False,
    },
    {
        "id": "fireworks:accounts/fireworks/models/deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "supports_images": False,
    },
    {
        "id": "fireworks:accounts/fireworks/models/glm-5p2",
        "label": "GLM 5.2",
        "efforts": ["none", "high", "max"],
        "default_effort": "high",
        "supports_images": False,
    },
]

SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in SUPPORTED_MODELS)

FABLE_MODEL_IDS: frozenset[str] = frozenset(
    m["id"] for m in SUPPORTED_MODELS if m["id"].startswith("anthropic:claude-fable")
)

ProfileLoader = Callable[[str], Mapping[str, object]]

# LangChain partner packages expose ``_get_default_model_profile`` — the same
# accessor that populates ``ChatModel.profile`` from bundled models.dev data —
# so context windows come from LangChain profiles instead of hardcoded numbers.
_PROFILE_LOADER_MODULES: dict[str, str] = {
    "anthropic": "langchain_anthropic.chat_models",
    "fireworks": "langchain_fireworks.chat_models",
    "google_genai": "langchain_google_genai.chat_models",
    "openai": "langchain_openai.chat_models.base",
}


@cache
def _profile_loader(provider: str) -> ProfileLoader | None:
    module_path = _PROFILE_LOADER_MODULES.get(provider)
    if module_path is None:
        return None
    try:
        module = import_module(module_path)
    except ImportError:
        return None
    loader = getattr(module, "_get_default_model_profile", None)
    if not callable(loader):
        return None
    return cast(ProfileLoader, loader)


@lru_cache(maxsize=512)
def model_profile_context_window(model_id: str) -> int | None:
    provider, _, model_name = model_id.partition(":")
    if not provider or not model_name:
        return None
    loader = _profile_loader(provider)
    if loader is None:
        return None
    profile = loader(model_name)
    context_window = profile.get("max_input_tokens")
    if isinstance(context_window, int) and context_window > 0:
        return context_window
    return None


def models_with_profile_context_windows(models: Sequence[ModelOption]) -> list[ModelOption]:
    enriched: list[ModelOption] = []
    for model in models:
        option: ModelOption = {
            "id": model["id"],
            "label": model["label"],
            "efforts": model["efforts"],
            "default_effort": model["default_effort"],
            "supports_images": model["supports_images"],
        }
        context_window = model_profile_context_window(model["id"])
        if context_window is not None:
            option["context_window"] = context_window
        enriched.append(option)
    return enriched


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


DEFAULT_MODEL_ID: str = "openai:gpt-5.5"
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


def provider_fallback_pair(model_id: object, effort: object = None) -> tuple[str, str] | None:
    """Newest supported ``(model_id, effort)`` for the same provider/family.

    Keeps a stored selection on its original provider when its exact id has
    dropped out of the supported set (e.g. an Opus minor-version bump), preferring
    the same Claude family when available instead of falling through to the
    cross-provider global default. Preserves ``effort`` when the fallback model
    supports it, otherwise uses that model's default effort. Returns ``None`` when
    no supported model shares the provider.
    """
    if not isinstance(model_id, str):
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
    """Hardcoded fallback (model_id, reasoning_effort) used when no team default is set."""
    if DEFAULT_MODEL_ID in SUPPORTED_MODEL_IDS and model_supports_effort(
        DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT
    ):
        return DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT
    first = SUPPORTED_MODELS[0]
    return first["id"], first["default_effort"]


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
