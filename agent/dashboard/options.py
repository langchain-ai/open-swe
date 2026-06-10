"""Supported models and reasoning efforts surfaced in the profile editor."""

from __future__ import annotations

from typing import TypedDict


class ModelOption(TypedDict):
    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool


SUPPORTED_MODELS: list[ModelOption] = [
    {
        "id": "anthropic:claude-opus-4-8",
        "label": "Opus 4.8",
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
        "id": "google_genai:gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "efforts": ["minimal", "low", "medium", "high"],
        "default_effort": "medium",
        "supports_images": True,
    },
    {
        "id": "fireworks:accounts/fireworks/models/kimi-k2p6",
        "label": "Kimi K2.6",
        "efforts": ["none", "low", "medium", "high"],
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
        "id": "fireworks:accounts/fireworks/models/glm-5p1",
        "label": "GLM 5.1",
        "efforts": ["none", "low", "medium", "high"],
        "default_effort": "high",
        "supports_images": False,
    },
]

SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in SUPPORTED_MODELS)

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
    """Newest supported ``(model_id, effort)`` for the same provider as ``model_id``.

    Keeps a stored selection on its original provider when its exact id has
    dropped out of the supported set (e.g. an Opus minor-version bump), instead
    of falling through to the cross-provider global default. Preserves ``effort``
    when the fallback model supports it, otherwise uses that model's default
    effort. Returns ``None`` when no supported model shares the provider.
    """
    if not isinstance(model_id, str):
        return None
    provider = _provider_of(model_id)
    if provider is None:
        return None
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
