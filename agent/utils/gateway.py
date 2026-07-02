"""LangSmith LLM Gateway routing for model construction.

The LLM Gateway (https://docs.langchain.com/langsmith/llm-gateway) proxies
provider calls through LangSmith: the client authenticates with a LangSmith API
key and the gateway resolves the real provider key from workspace Provider
Secrets, enforcing spend/PII/secrets policies and tracing every call. Routing is
opt-in via ``LANGSMITH_GATEWAY_ENABLED`` (deployment default) or the
``gateway_enabled`` team setting, and is applied centrally in
:func:`agent.utils.model.make_model`.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_BASE_URL = "https://gateway.smith.langchain.com"

# Provider prefix -> base-URL suffix appended to the gateway host. Each suffix
# matches the SDK's own path handling: the OpenAI/Fireworks SDKs append
# ``/chat/completions`` to a ``/v1`` base, the Anthropic SDK appends
# ``/v1/messages`` to a bare host, and the google-genai SDK appends
# ``/<api_version>/models/...`` to a bare host. Vertex (``google_vertexai``, which
# uses service-account auth rather than a bearer key) and any other provider are
# not routed and call the provider directly.
_GATEWAY_PROVIDER_PATHS: dict[str, str] = {
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
    "fireworks": "/fireworks/v1",
    "google_genai": "/gemini",
}


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _langsmith_api_key() -> str | None:
    """LangSmith API key used to authenticate gateway calls.

    Mirrors ``agent.integrations.langsmith._get_langsmith_api_key``:
    ``LANGSMITH_API_KEY`` first, then ``LANGSMITH_API_KEY_PROD`` for LangGraph
    Cloud deployments where ``LANGSMITH_API_KEY`` is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def gateway_base_url() -> str:
    """Gateway host, overridable via ``LANGSMITH_GATEWAY_BASE_URL`` (regional/self-hosted)."""
    return (os.environ.get("LANGSMITH_GATEWAY_BASE_URL") or DEFAULT_GATEWAY_BASE_URL).rstrip("/")


def gateway_env_default() -> bool:
    """Deployment-level default for gateway routing (``LANGSMITH_GATEWAY_ENABLED``)."""
    return _env_bool(os.environ.get("LANGSMITH_GATEWAY_ENABLED"))


def gateway_openai_use_responses() -> bool:
    """Whether gateway-routed OpenAI keeps the Responses API.

    Defaults to ``False``: the gateway's documented OpenAI surface is Chat
    Completions, and an HTTPS proxy can't carry open-swe's default ``wss://``
    Responses stream. Set ``LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES=true`` only if
    the gateway proxies ``/v1/responses``.
    """
    return _env_bool(os.environ.get("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES"))


def resolve_gateway_enabled(team_value: bool | None) -> bool:
    """Combine the team-settings toggle with the env default.

    A team value of ``True``/``False`` is authoritative; ``None`` inherits the
    ``LANGSMITH_GATEWAY_ENABLED`` deployment default.
    """
    if team_value is None:
        return gateway_env_default()
    return team_value


def _provider_of(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def gateway_overrides(model_id: str) -> dict[str, object] | None:
    """``init_chat_model`` kwargs that route ``model_id`` through the gateway.

    Returns ``None`` (so the caller keeps talking to the provider directly) when
    the provider isn't routable through the gateway or no LangSmith API key is
    available — both cases are logged rather than raised, so a run never fails
    just because gateway routing couldn't be applied.
    """
    provider = _provider_of(model_id)
    path = _GATEWAY_PROVIDER_PATHS.get(provider)
    if path is None:
        logger.warning(
            "LangSmith gateway enabled but provider %r is not routed; calling it directly",
            provider,
        )
        return None
    api_key = _langsmith_api_key()
    if not api_key:
        logger.warning(
            "LangSmith gateway enabled but no LANGSMITH_API_KEY(_PROD) is set; "
            "calling the provider directly"
        )
        return None
    overrides: dict[str, object] = {
        "base_url": f"{gateway_base_url()}{path}",
        "api_key": api_key,
    }
    if provider == "openai":
        # An HTTPS proxy can't carry the wss:// Responses stream make_model sets by
        # default; route Chat Completions unless the deployment opts back in.
        overrides["use_responses_api"] = gateway_openai_use_responses()
    return overrides
