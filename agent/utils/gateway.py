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

from ..config import (
    langsmith_credentials,
    langsmith_gateway_enabled_default,
    langsmith_gateway_openai_use_responses,
)

logger = logging.getLogger(__name__)

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
    "fireworks": "/fireworks",
    "google_genai": "/gemini",
}


def resolve_gateway_enabled(team_value: bool | None) -> bool:
    """Combine the team-settings toggle with the env default.

    A team value of ``True``/``False`` is authoritative; ``None`` inherits the
    ``LANGSMITH_GATEWAY_ENABLED`` deployment default.
    """
    if team_value is None:
        return langsmith_gateway_enabled_default()
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
    credentials = langsmith_credentials("gateway")
    if credentials is None:
        logger.warning(
            "LangSmith gateway enabled but no LANGSMITH_GATEWAY_API_KEY or "
            "LANGSMITH_API_KEY(_PROD) is set; "
            "calling the provider directly"
        )
        return None
    api_key, base_url = credentials
    overrides: dict[str, object] = {
        "base_url": f"{base_url}{path}",
        "api_key": api_key,
    }
    if provider == "openai":
        # Use HTTPS Responses through the gateway by default; tool-calling OpenAI
        # reasoning models reject reasoning_effort on Chat Completions.
        overrides["use_responses_api"] = langsmith_gateway_openai_use_responses()
    return overrides
