"""Supported models and reasoning efforts surfaced in the profile editor.

The table itself is shared with the MDA runtime (``runtime_api.models``); what the
deployment and global settings choose from it is the backend's.
"""

from runtime_api.models import (
    SUPPORTED_MODEL_IDS,
    SUPPORTED_MODELS,
    ModelOption,
    model_context_window,
    model_supports_effort,
    model_supports_images,
)

from openswe.config import ENV

__all__ = [
    "DEFAULT_MODEL_EFFORT",
    "DEFAULT_MODEL_ID",
    "FABLE_MODEL_IDS",
    "NON_DEFAULT_MODEL_IDS",
    "SUPPORTED_MODELS",
    "SUPPORTED_MODEL_IDS",
    "ModelOption",
    "available_requested_models",
    "default_model_pair",
    "default_vision_model_pair",
    "fable_disabled_fallback",
    "gate_fable_model",
    "model_context_window",
    "model_supports_effort",
    "model_supports_images",
    "normalize_model_choice",
]


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


def fable_disabled_fallback(effort: object = None) -> tuple[str, str]:
    """Newest supported non-Fable Anthropic model (keeps the Claude family),
    else the global default. Substitutes a Fable selection when Fable is
    disabled instance-wide, preserving ``effort`` when the fallback supports it."""
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


DEFAULT_MODEL_ID: str = "openai:gpt-6.1-sol"
DEFAULT_MODEL_EFFORT: str = "medium"


def _fallback_effort_for(model: ModelOption, effort: object) -> str | None:
    if isinstance(effort, str) and effort in model["efforts"]:
        return effort
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


def default_model_pair() -> tuple[str, str]:
    """Deployment fallback used when the global settings do not set a default."""
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
