"""LangSmith deployment settings that Hosts inject under more than one name.

Hosted revisions inject the legacy ``LANGCHAIN_*`` names (Cloud SaaS omits the
reserved ``LANGSMITH_ENDPOINT``), while self-hosted and BYOC Hosts may inject
only ``LANGSMITH_*``. Each resolver returns the first nonblank name, trimmed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"


def _first_set(env: Mapping[str, str] | None, *names: str) -> str | None:
    resolved = os.environ if env is None else env
    return next((v for name in names if (v := (resolved.get(name) or "").strip())), None)


def resolve_server_api_key(env: Mapping[str, str] | None = None) -> str:
    """The deployment key: ``LANGSMITH_API_KEY``, then ``LANGCHAIN_API_KEY``, else ``""``."""
    return _first_set(env, "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY") or ""


def resolve_platform_injected_api_key(env: Mapping[str, str] | None = None) -> str:
    """The key the platform injected, even when a user overrode ``LANGSMITH_API_KEY``."""
    return _first_set(env, "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY") or ""


def resolve_langsmith_endpoint(env: Mapping[str, str] | None = None) -> str:
    """The LangSmith API origin, else SaaS."""
    return _first_set(env, "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT") or DEFAULT_LANGSMITH_ENDPOINT


def resolve_workspace_id(env: Mapping[str, str] | None = None) -> str | None:
    """The workspace a user set, else the one the platform runs the deploy in."""
    return _first_set(env, "LANGSMITH_WORKSPACE_ID", "LANGSMITH_TENANT_ID")


def resolve_tenant_workspace_id(env: Mapping[str, str] | None = None) -> str | None:
    """The workspace the platform runs the deploy in, else the one a user set."""
    return _first_set(env, "LANGSMITH_TENANT_ID", "LANGSMITH_WORKSPACE_ID")
