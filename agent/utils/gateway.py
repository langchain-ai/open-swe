"""LangSmith LLM Gateway routing for model construction.

The LLM Gateway (https://docs.langchain.com/langsmith/llm-gateway) proxies
provider calls through LangSmith: the client authenticates with a LangSmith API
key and the gateway resolves the real provider key from workspace Provider
Secrets, enforcing spend/PII/secrets policies and tracing every call. Routing is
opt-in via ``LANGSMITH_GATEWAY_ENABLED`` (deployment default) or the
``gateway_enabled`` team setting, and is applied centrally in
:func:`agent.utils.model.make_model`.
"""

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.config import ENV

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_BASE_URL = "https://gateway.smith.langchain.com"

# Provider prefix -> base-URL suffix appended to the gateway host. Each suffix
# matches the SDK's own path handling: the OpenAI SDK appends
# ``/chat/completions`` to a ``/v1`` base, Fireworks appends
# ``/v1/chat/completions`` to a bare provider host, Anthropic appends
# ``/v1/messages`` to a bare host, and google-genai appends
# ``/<api_version>/models/...`` to a bare host. Vertex (``google_vertexai``, which
# uses service-account auth rather than a bearer key) and any other provider are
# not routed and call the provider directly.
_GATEWAY_PROVIDER_PATHS: dict[str, str] = {
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
    "baseten": "/baseten/v1",
    "fireworks": "/fireworks",
    "google_genai": "/gemini",
}


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _langsmith_api_key() -> str | None:
    """LangSmith API key used to authenticate gateway calls.

    Prefer a gateway-specific key, then the standard LangSmith key. The key
    LangGraph Platform injects as ``LANGSMITH_API_KEY`` can lack the
    ``gateway:invoke`` permission, which is what ``LANGSMITH_GATEWAY_API_KEY`` is for.
    """
    return ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()


def gateway_base_url() -> str:
    """Gateway host, overridable via ``LANGSMITH_GATEWAY_BASE_URL`` (regional/self-hosted)."""
    return (ENV.LANGSMITH_GATEWAY_BASE_URL.optional() or DEFAULT_GATEWAY_BASE_URL).rstrip("/")


def gateway_env_default() -> bool:
    """Deployment-level default for gateway routing.

    ``LANGSMITH_GATEWAY_ENABLED`` decides when set. Otherwise a dedicated
    ``LANGSMITH_GATEWAY_API_KEY`` is the signal: that key exists for nothing
    else, so configuring it means routing through the gateway.
    """
    explicit = ENV.LANGSMITH_GATEWAY_ENABLED.optional()
    if explicit is not None:
        return _env_bool(explicit)
    return bool(ENV.LANGSMITH_GATEWAY_API_KEY.optional())


def gateway_openai_use_responses() -> bool:
    """Whether gateway-routed OpenAI keeps the Responses API.

    Defaults to ``True`` because OpenAI reasoning models with tool calls reject
    ``reasoning_effort`` on Chat Completions. Set
    ``LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES=false`` only for deployments that
    need to force Chat Completions through the gateway.
    """
    raw = ENV.LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES.optional()
    if raw is None:
        return True
    return _env_bool(raw)


def resolve_gateway_enabled(team_value: bool | None) -> bool:
    """Combine the team-settings toggle with the env default.

    A team value of ``True``/``False`` is authoritative; ``None`` inherits the
    ``LANGSMITH_GATEWAY_ENABLED`` deployment default.
    """
    if team_value is None:
        return gateway_env_default()
    return team_value


def _sanitize_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))


def gateway_configuration_summary(team_value: bool | None, model_id: str) -> dict[str, Any]:
    """Safe metadata describing the effective endpoint for an administrator."""
    explicit = ENV.LANGSMITH_GATEWAY_ENABLED.optional()
    if team_value is not None:
        enabled = team_value
        reason = "workspace setting"
    elif explicit is not None:
        enabled = _env_bool(explicit)
        reason = "LANGSMITH_GATEWAY_ENABLED"
    elif ENV.LANGSMITH_GATEWAY_API_KEY.is_set():
        enabled = True
        reason = "LANGSMITH_GATEWAY_API_KEY is set"
    else:
        enabled = False
        reason = "no LangSmith Gateway environment default is set"

    provider = _provider_of(model_id)
    path = _GATEWAY_PROVIDER_PATHS.get(provider)
    credential_sources: list[str] = []
    endpoint_kind = "direct_provider"
    endpoint: str | None = None
    if enabled and path and _langsmith_api_key():
        endpoint_kind = "langsmith_gateway"
        endpoint = _sanitize_endpoint(f"{gateway_base_url()}{path}")
        source = ENV.LANGSMITH_GATEWAY_API_KEY.source() or ENV.LANGSMITH_API_KEY.source()
        if source:
            credential_sources.append(source)
    elif provider == "openai":
        custom_endpoint = ENV.OPENAI_BASE_URL.optional()
        if custom_endpoint:
            endpoint_kind = "custom_endpoint"
            endpoint = _sanitize_endpoint(custom_endpoint)
        else:
            endpoint = OPENAI_PROVIDER_ENDPOINT
        source = ENV.OPENAI_API_KEY.source()
        if source:
            credential_sources.append(source)
    else:
        credential = {
            "anthropic": ENV.ANTHROPIC_API_KEY,
            "baseten": ENV.BASETEN_API_KEY,
            "fireworks": ENV.FIREWORKS_API_KEY,
            "google_genai": ENV.GOOGLE_API_KEY,
        }.get(provider)
        source = credential.source() if credential else None
        if source:
            credential_sources.append(source)

    return {
        "model_id": model_id,
        "override_enabled": enabled,
        "resolution_reason": reason,
        "endpoint_kind": endpoint_kind,
        "endpoint": endpoint,
        "credential_sources": credential_sources,
        "restart_required": False,
    }


OPENAI_PROVIDER_ENDPOINT = "https://api.openai.com/v1"


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
