"""Sandbox capabilities for the associated thread's tools."""

import shlex
from urllib.parse import urlencode, urlsplit

import jwt
from deepagents.backends.protocol import SandboxBackendProtocol
from fastapi import HTTPException
from langgraph_sdk import get_client
from pydantic import BaseModel, Field, ValidationError

from agent.config import ENV
from agent.utils.dashboard_links import dashboard_api_base_url

TOOLS_PATH = "/sandbox-tools"
TOOLS_HEADER = "X-Open-SWE-Tools-Token"
TOOLS_RULE = "open-swe-thread-tools"
TOOLS_URL_FILE = "/tmp/open-swe-tools-url"
TOOLS_AUDIENCE = "open-swe-sandbox-tools"


class ToolAccess(BaseModel):
    thread_id: str = Field(strict=True, min_length=1)
    sandbox_id: str = Field(strict=True, min_length=1)


def tools_base_url() -> str | None:
    base = dashboard_api_base_url()
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return base.rstrip("/") + TOOLS_PATH


async def issue_tool_access(thread_id: str, sandbox_id: str) -> tuple[str, str] | None:
    secret = ENV.DASHBOARD_JWT_SECRET.optional()
    url = tools_base_url()
    if not secret or not url:
        return None
    access = ToolAccess(thread_id=thread_id, sandbox_id=sandbox_id)
    token = jwt.encode({"aud": TOOLS_AUDIENCE, **access.model_dump()}, secret, algorithm="HS256")
    return url, token


async def authenticate_tool_access(token: str | None) -> ToolAccess:
    secret = ENV.DASHBOARD_JWT_SECRET.optional()
    if not token or len(token) > 4096 or not secret:
        raise HTTPException(401, "Invalid sandbox capability")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=TOOLS_AUDIENCE,
            options={"require": ["aud", "thread_id", "sandbox_id"]},
        )
        access = ToolAccess.model_validate(claims)
    except (jwt.PyJWTError, ValidationError) as exc:
        raise HTTPException(401, "Invalid sandbox capability") from exc
    try:
        thread = await get_client().threads.get(access.thread_id)
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 404:
            raise HTTPException(401, "Invalid sandbox capability") from exc
        raise
    if (thread.get("metadata") or {}).get("sandbox_id") != access.sandbox_id:
        raise HTTPException(401, "Invalid sandbox capability")
    return access


async def tool_proxy_rule(thread_id: str, sandbox_id: str) -> dict[str, object] | None:
    access = await issue_tool_access(thread_id, sandbox_id)
    if access is None:
        return None
    url, token = access
    return {
        "name": TOOLS_RULE,
        "match_hosts": [urlsplit(url).hostname],
        "headers": [{"name": TOOLS_HEADER, "type": "opaque", "value": token}],
        "env_vars": {"OPEN_SWE_TOOLS_URL": url},
    }


async def provision_tool_url(thread_id: str, backend: SandboxBackendProtocol) -> None:
    if ENV.SANDBOX_TYPE.get() == "langsmith":
        return
    access = await issue_tool_access(thread_id, backend.id)
    if access is None:
        return
    url, token = access
    tokenized = f"{url}?{urlencode({'token': token})}"
    result = await backend.aexecute(
        f"set -e; umask 077; tools_url_tmp=$(mktemp {TOOLS_URL_FILE}.XXXXXX); "
        f'printf %s {shlex.quote(tokenized)} > "$tools_url_tmp"; '
        f'mv -f "$tools_url_tmp" {TOOLS_URL_FILE}'
    )
    if result.exit_code != 0:
        raise RuntimeError("Cannot provision sandbox tools URL")
