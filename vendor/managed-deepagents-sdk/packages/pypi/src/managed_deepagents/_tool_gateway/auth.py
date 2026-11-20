"""``auth: "langsmith"``: the contract between MDA and LangSmith Tool Gateway.

A server with it is a normal MCP server. MDA sends the deployment's LangSmith
key and workspace, and names whose credentials Tool Gateway uses: a profile's,
the run's person's, or with neither the agent's.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlsplit

from managed_deepagents._agent_auth import AgentAuthContext
from managed_deepagents._agent_auth_http import agent_auth_headers
from managed_deepagents._connections import _resolve_agent_auth_config
from managed_deepagents._langsmith_env import (
    resolve_platform_injected_api_key,
    resolve_tenant_workspace_id,
)

#: Headers that decide whose credentials Tool Gateway uses. LangSmith auth sets
#: them, so a declaration cannot.
RESERVED_HEADERS = (
    "authorization",
    "x-api-key",
    "x-tenant-id",
    "x-langsmith-identity",
    "x-langsmith-profile",
)


def uses_langsmith_auth(server: object) -> bool:
    if not isinstance(server, dict):
        return False
    if server.get("auth") is not None:
        return True
    return "connection" not in server and is_tool_gateway_url(server.get("url"))


def langsmith_profile(server: object) -> str | None:
    """The profile whose credentials every run uses, if the server names one."""
    auth = server.get("auth") if isinstance(server, dict) else None
    return auth.get("profile") if isinstance(auth, dict) else None


def langsmith_headers(context: AgentAuthContext, profile: str | None) -> dict[str, str]:
    """The deployment's key and workspace, and whose credentials Tool Gateway uses:
    the profile's, the run's person's, or with neither the agent's."""
    headers = agent_auth_headers(api_key=context.api_key, workspace_id=context.workspace_id)
    # The MCP transport sets its own Accept and Content-Type.
    del headers["accept"], headers["content-type"]
    # Tool Gateway refuses a profile together with a person.
    if profile:
        headers["x-langsmith-profile"] = profile
    elif context.principal_id:
        headers["x-langsmith-identity"] = context.principal_id
    return headers


def tool_gateway_context(caller: AgentAuthContext | None) -> AgentAuthContext:
    """Who Tool Gateway acts for: ``caller``'s person, or the agent when None.

    Always the key and workspace the platform injected: Tool Gateway acts as the
    agent environment bound to that key when no principal is named, so the key is
    never a user's ``LANGSMITH_API_KEY`` override.
    """
    if caller is None:
        config = _resolve_agent_auth_config(os.environ)
        caller = AgentAuthContext(
            base_url=config.base_url,
            agent_id=config.deployment_id,
            principal_id="",
            api_key=config.api_key,
            workspace_id=config.workspace_id,
        )
    return replace(
        caller,
        api_key=resolve_platform_injected_api_key(os.environ) or caller.api_key,
        workspace_id=resolve_tenant_workspace_id(os.environ) or caller.workspace_id,
    )


def refuse_other_origin(name: str, server: Mapping[str, object], base_url: str) -> None:
    """The key goes only to the deployment's LangSmith API: a server elsewhere
    fails, or is skipped with a warning when its auth is only implied by its URL."""
    message = f'MCP server "{name}" uses LangSmith auth, but its URL is not on {base_url}'
    if server.get("auth") is not None:
        raise ValueError(message)
    warnings.warn(f"{message}, so it was skipped.", RuntimeWarning, stacklevel=3)


# Temporary: Tool Gateway URLs declared before ``auth`` existed get it implied.

#: Tool Gateway's MCP paths. A server on one with neither ``auth`` nor
#: ``connection`` was declared before ``auth`` existed, and still gets
#: LangSmith auth until that default is removed.
_TOOL_GATEWAY_PATH = re.compile(r"/v1/managed-tools/(?:servers|kits|gateways)/[^/]+/mcp/?")


def is_tool_gateway_url(url: object) -> bool:
    return isinstance(url, str) and bool(_TOOL_GATEWAY_PATH.fullmatch(urlsplit(url).path))
