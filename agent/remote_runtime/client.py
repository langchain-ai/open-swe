"""Clients for graphs hosted in another deployment, and the run token dispatch stamps for them."""

from urllib.parse import urlsplit

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from pydantic import JsonValue, TypeAdapter

from agent.config import ENV
from agent.remote_runtime.tokens import (
    RUNTIME_TOKEN_CONFIG_KEY,
    RemoteRun,
    runtime_tokens_configured,
    sign_runtime_token,
)

_configurable = TypeAdapter(dict[str, JsonValue])


class RemoteRuntimeConfigurationError(RuntimeError):
    """A remote runtime is configured in a way this backend cannot safely call."""


def _remote_url(assistant_id: str) -> str | None:
    if assistant_id == "reviewer":
        return ENV.REVIEWER_RUNTIME_URL.optional()
    return None


def remote_runtime_client(assistant_id: str) -> LangGraphClient | None:
    """The client for the deployment hosting ``assistant_id``, or None to run it locally."""
    url = _remote_url(assistant_id)
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteRuntimeConfigurationError(f"The {assistant_id} runtime URL is not absolute")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RemoteRuntimeConfigurationError(f"The {assistant_id} runtime URL must use HTTPS")
    if not runtime_tokens_configured():
        raise RemoteRuntimeConfigurationError(
            "REMOTE_RUNTIME_TOKEN_SECRET is required to run graphs in another deployment"
        )
    return get_client(url=url.rstrip("/"), api_key=ENV.REVIEWER_RUNTIME_API_KEY.optional())


def stamp_runtime_token(
    configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
) -> dict[str, JsonValue]:
    """Return ``configurable`` with the signed token its remote run presents to the tool server."""
    claims = {key: value for key, value in configurable.items() if key != RUNTIME_TOKEN_CONFIG_KEY}
    token = sign_runtime_token(
        RemoteRun(
            thread_id=thread_id,
            assistant_id=assistant_id,
            configurable=_configurable.validate_python(claims),
        )
    )
    return {**claims, RUNTIME_TOKEN_CONFIG_KEY: token}
