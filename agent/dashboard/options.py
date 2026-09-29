"""Supported models and reasoning efforts surfaced in the profile editor."""

from collections.abc import Sequence
from typing import NotRequired, TypedDict

from agent.config import ENV
from agent.model_catalog import CATALOG


class ModelOption(TypedDict):
    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool
    can_be_default: NotRequired[bool]
    context_window: NotRequired[int | None]


SUPPORTED_MODELS: list[ModelOption] = []
SUPPORTED_MODEL_IDS: set[str] = set()
NON_DEFAULT_MODEL_IDS: set[str] = set()
DEPRECATED_MODEL_IDS: set[str] = set()


def update_catalog_options() -> None:
    models: list[ModelOption] = []
    for model_id, model in CATALOG.items():
        if model.status == "deprecated":
            continue
        efforts = model.efforts()
        option: ModelOption = {
            "id": model_id,
            "label": model.name,
            "efforts": efforts,
            "default_effort": "medium" if "medium" in efforts else efforts[0],
            "supports_images": "image" in model.modalities.input,
            "context_window": model.limit.input or model.limit.context,
        }
        models.append(option)
    SUPPORTED_MODELS[:] = models
    for target, values in (
        (SUPPORTED_MODEL_IDS, {m["id"] for m in models}),
        (NON_DEFAULT_MODEL_IDS, {m["id"] for m in models if not m.get("can_be_default", True)}),
        (
            DEPRECATED_MODEL_IDS,
            {model_id for model_id, model in CATALOG.items() if model.status == "deprecated"},
        ),
    ):
        target.clear()
        target.update(values)


update_catalog_options()


def available_requested_models() -> dict[str, ModelOption]:
    return {model["id"]: model for model in SUPPORTED_MODELS}


def model_profile_with_context_override(model_id: str) -> dict[str, object] | None:
    model = CATALOG.get(model_id)
    if model is None:
        return None
    return {
        "max_input_tokens": model.limit.input or model.limit.context,
        "max_output_tokens": model.limit.output,
        "image_inputs": "image" in model.modalities.input,
        "tool_calling": model.tool_call,
    }


def model_profile_context_window(model_id: str) -> int | None:
    model = CATALOG.get(model_id)
    return (model.limit.input or model.limit.context) if model else None


def models_with_profile_context_windows(models: Sequence[ModelOption]) -> list[ModelOption]:
    return list(models)


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


def is_deprecated_model(model_id: object) -> bool:
    return isinstance(model_id, str) and model_id in DEPRECATED_MODEL_IDS


def canonical_model_pair(model_id: object, effort: object = None) -> tuple[str, str] | None:
    if not isinstance(model_id, str):
        return None
    model = next((m for m in SUPPORTED_MODELS if m["id"] == model_id), None)
    if (
        model is None
        or effort in model["efforts"]
        or effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
    ):
        return None
    return model_id, model["default_effort"]


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
    if ENV.LLM_MODEL_ID.optional():
        raise ValueError(f"Unsupported default LLM_MODEL_ID: {model_id!r}")
    provider = model_id.split(":", 1)[0]
    model = next(
        m
        for m in SUPPORTED_MODELS
        if m["id"].startswith(provider + ":") and m.get("can_be_default", True)
    )
    return model["id"], _fallback_effort_for(model, DEFAULT_MODEL_EFFORT) or model["default_effort"]


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
