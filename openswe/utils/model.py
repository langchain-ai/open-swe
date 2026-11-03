"""The chat models the backend builds, as the MDA runtime builds them (``runtime_api.models``)."""

from runtime_api.models import (
    BASETEN_BASE_URL,
    DEFAULT_LLM_REASONING,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OPENAI_RESPONSES_WS_BASE_URL,
    AnthropicEffort,
    AnthropicThinking,
    FireworksReasoningEffort,
    GoogleThinkingLevel,
    ModelKwargs,
    OpenAIReasoning,
    OpenAIReasoningEffort,
    anthropic_effort_for,
    anthropic_thinking_for,
    fallback_model_id_for,
    fireworks_reasoning_effort_for,
    google_thinking_level_for,
    make_model,
    model_request_defaults,
    openai_reasoning_for,
    provider_model_kwargs,
    supports_google_thinking_level,
)

from openswe.config import ENV
from openswe.dashboard.options import DEFAULT_MODEL_ID

__all__ = [
    "BASETEN_BASE_URL",
    "DEFAULT_LLM_REASONING",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "OPENAI_RESPONSES_WS_BASE_URL",
    "AnthropicEffort",
    "AnthropicThinking",
    "FireworksReasoningEffort",
    "GoogleThinkingLevel",
    "ModelKwargs",
    "OpenAIReasoning",
    "OpenAIReasoningEffort",
    "anthropic_effort_for",
    "anthropic_thinking_for",
    "fallback_model_id_for",
    "fireworks_reasoning_effort_for",
    "google_thinking_level_for",
    "make_model",
    "model_request_defaults",
    "openai_reasoning_for",
    "provider_model_kwargs",
    "supports_google_thinking_level",
    "validate_local_dev_llm_config",
]


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
