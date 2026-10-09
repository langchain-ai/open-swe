"""The private thread owner's LangSmith access, injected by the sandbox proxy.

Tools that only accept ``LANGSMITH_API_KEY`` (the ``langsmith`` and ``mda`` CLIs, the SDKs)
see a placeholder; the proxy swaps in a short-lived key minted from the owner's
Sign in with LangSmith connection, so the real key never enters the sandbox.
"""

import logging
from urllib.parse import urlsplit

from openswe.dashboard.langsmith_oauth import (
    LangSmithOAuthError,
    langsmith_issuer,
    langsmith_sandbox_api_key,
)
from openswe.sandboxes.tool_access import sandbox_host_metadata

logger = logging.getLogger(__name__)

LANGSMITH_RULE = "open-swe-langsmith"
LANGSMITH_API_KEY_PLACEHOLDER = "proxy-injected"
_API_HOST_SUFFIX = "api.smith.langchain.com"


def _langsmith_hosts(endpoint: str) -> list[str]:
    """The LangSmith API, plus its sibling deployments backend that ``mda deploy`` calls."""
    host = (urlsplit(endpoint).hostname or "").lower()
    if not host:
        return []
    if host == _API_HOST_SUFFIX or host.endswith(f".{_API_HOST_SUFFIX}"):
        return [host, host.removesuffix("smith.langchain.com") + "host.langchain.com"]
    return [host]


async def _private_owner(thread_id: str) -> str | None:
    metadata = await sandbox_host_metadata(thread_id)
    owner = metadata.get("owner_login")
    if (
        metadata.get("visibility") != "private"
        or metadata.get("owner_type") == "system"
        or not isinstance(owner, str)
        or not owner.strip()
    ):
        return None
    return owner.strip()


async def langsmith_proxy_rule(thread_id: str) -> dict[str, object] | None:
    """Only a private thread's sandbox acts as its owner, and only once they connect LangSmith."""
    owner = await _private_owner(thread_id)
    if owner is None:
        return None
    try:
        api_key = await langsmith_sandbox_api_key(owner)
    except LangSmithOAuthError:
        logger.warning(
            "Cannot mint a LangSmith key for the sandbox",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
        return None
    endpoint = langsmith_issuer()
    hosts = _langsmith_hosts(endpoint)
    if api_key is None or not hosts:
        return None
    return {
        "name": LANGSMITH_RULE,
        "match_hosts": hosts,
        "headers": [{"name": "X-Api-Key", "type": "opaque", "value": api_key}],
        "env_vars": {
            "LANGSMITH_API_KEY": LANGSMITH_API_KEY_PLACEHOLDER,
            "LANGSMITH_ENDPOINT": endpoint,
        },
    }
