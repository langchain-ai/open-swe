"""Supported models and reasoning efforts surfaced in the profile editor.

The table itself is shared with the MDA runtime (``runtime_api.models``); what the
deployment and workspace choose from it is the backend's.
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
    "DEPRECATED_MODEL_IDS",
    "DEPRECATED_MODEL_REPLACEMENTS",
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
    "provider_fallback_pair",
    "validate_local_dev_llm_config",
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


def validate_local_dev_llm_config() -> None:
    """Validate API keys for the locally configured default model.

    This check only runs in localhost development environments and is
    intended to catch missing credentials for the default model specified
    via LLM_MODEL_ID/DEFAULT_MODEL_ID. Runtime model selection may come
    from workspace, profile, or thread configuration and is not validated here.

    Only an explicitly configured localhost dashboard URL counts: the derived
    default follows LANGGRAPH_URL, which is unset (so localhost) on a fresh
    LangGraph Platform deployment until its URL exists, and failing startup
    there would leave the deployment unable to ever get one.
    """
    dashboard_url = ENV.DASHBOARD_BASE_URL.optional() or ""
    if not dashboard_url.startswith("http://localhost"):
        return

    model_id = ENV.LLM_MODEL_ID.get(DEFAULT_MODEL_ID)

    if model_id.startswith("openai:") and not ENV.OPENAI_API_KEY.optional():
        raise ValueError(f"OPENAI_API_KEY is required for configured model {model_id}")
    elif model_id.startswith("anthropic:") and not ENV.ANTHROPIC_API_KEY.optional():
        raise ValueError(f"ANTHROPIC_API_KEY is required for configured model {model_id}")
    elif model_id.startswith("google_genai:") and not ENV.GOOGLE_API_KEY.optional():
        raise ValueError(f"GOOGLE_API_KEY is required for configured model {model_id}")
    elif model_id.startswith("groq:") and not ENV.GROQ_API_KEY.optional():
        raise ValueError(f"GROQ_API_KEY is required for configured model {model_id}")
    elif model_id.startswith("fireworks:") and not ENV.FIREWORKS_API_KEY.optional():
        raise ValueError(f"FIREWORKS_API_KEY is required for configured model {model_id}")
