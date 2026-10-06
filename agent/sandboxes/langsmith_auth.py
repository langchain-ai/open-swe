"""Authenticate the LangSmith CLI in a private thread's sandbox as the thread's owner.

The sandbox proxy calls back here for the owner's LangSmith OAuth token, so the token
never enters the sandbox and is refused once the thread stops being private.
"""

import logging
from collections.abc import Mapping
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Response
from langgraph_sdk import get_client
from pydantic import BaseModel

from agent.dashboard.langsmith_oauth import (
    LANGSMITH_KEY,
    langsmith_access_token,
    langsmith_issuer,
    langsmith_oauth_configured,
)
from agent.dashboard.oauth_credentials import load_credential
from agent.sandboxes.tool_access import (
    TOOLS_PATH,
    authenticate_tool_access,
    issue_tool_access,
    sandbox_host_thread_id,
    tools_base_url,
)
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

# Outside TOOLS_PATH's capability, which the proxy injects into every sandbox request
# to the dashboard host: a sandbox must never be able to call this itself.
LANGSMITH_CALLBACK_PATH = "/dashboard/api/sandbox-langsmith/credentials"
LANGSMITH_CALLBACK_HEADER = "X-Open-SWE-LangSmith-Callback-Token"
LANGSMITH_CALLBACK_AUDIENCE = "open-swe-sandbox-langsmith"
LANGSMITH_CALLBACK_TTL_SECONDS = 60
LANGSMITH_API_KEY_PLACEHOLDER = "open-swe-proxy-injected"


class ProxyCallbackRequest(BaseModel):
    host: str


class ProxyCallbackResponse(BaseModel):
    headers: dict[str, str]


class LangSmithProxyAuth(BaseModel):
    callback: dict[str, object]
    env_vars: dict[str, str]


def private_thread_owner(metadata: Mapping[str, object]) -> str | None:
    """The saved owner of a private user thread, the only person who can prompt it."""
    if metadata.get("visibility") != "private" or metadata.get("owner_type") == "system":
        return None
    owner = metadata.get("owner_login")
    return owner.strip() if isinstance(owner, str) and owner.strip() else None


def langsmith_callback_url() -> str | None:
    base = tools_base_url()
    return base.removesuffix(TOOLS_PATH) + LANGSMITH_CALLBACK_PATH if base else None


def _langsmith_host() -> str | None:
    return urlsplit(langsmith_issuer()).hostname


async def langsmith_proxy_auth(thread_id: str, sandbox_id: str) -> LangSmithProxyAuth | None:
    """The proxy callback for a private thread whose owner has connected LangSmith."""
    url = langsmith_callback_url()
    host = _langsmith_host()
    if not url or not host or not langsmith_oauth_configured():
        return None
    thread = await get_client().threads.get(await sandbox_host_thread_id(thread_id))
    owner = private_thread_owner(thread_metadata(thread))
    if owner is None or await load_credential(LANGSMITH_KEY, owner) is None:
        return None
    access = await issue_tool_access(thread_id, sandbox_id, audience=LANGSMITH_CALLBACK_AUDIENCE)
    if access is None:
        return None
    return LangSmithProxyAuth(
        callback={
            "match_hosts": [host],
            "url": url,
            "request_headers": [
                {"name": LANGSMITH_CALLBACK_HEADER, "type": "opaque", "value": access[1]}
            ],
            "ttl_seconds": LANGSMITH_CALLBACK_TTL_SECONDS,
        },
        env_vars={
            "LANGSMITH_API_KEY": LANGSMITH_API_KEY_PLACEHOLDER,
            "LANGSMITH_ENDPOINT": langsmith_issuer(),
        },
    )


router = APIRouter(prefix=LANGSMITH_CALLBACK_PATH, tags=["sandbox-langsmith"])


@router.post("", response_model=ProxyCallbackResponse)
async def resolve_langsmith_credentials(
    body: ProxyCallbackRequest,
    response: Response,
    token: Annotated[str | None, Header(alias=LANGSMITH_CALLBACK_HEADER)] = None,
) -> ProxyCallbackResponse:
    response.headers["Cache-Control"] = "no-store"
    access = await authenticate_tool_access(token, audience=LANGSMITH_CALLBACK_AUDIENCE)
    if body.host.lower() != _langsmith_host():
        return ProxyCallbackResponse(headers={})
    thread = await get_client().threads.get(access.thread_id)
    owner = private_thread_owner(thread_metadata(thread))
    access_token = await langsmith_access_token(owner) if owner else None
    if not access_token:
        logger.info(
            "No LangSmith credential for sandbox proxy callback",
            extra={"thread_id": access.thread_id, "private_owner": owner is not None},
        )
        return ProxyCallbackResponse(headers={})
    # The placeholder X-Api-Key would take precedence over the bearer token, so blank it.
    return ProxyCallbackResponse(
        headers={"Authorization": f"Bearer {access_token}", "X-Api-Key": ""}
    )
