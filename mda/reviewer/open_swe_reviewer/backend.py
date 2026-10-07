"""Calls to the Open SWE backend's tool server, made as the current run.

Dispatch stamps a signed run token on every run it starts here. Each call sends it
as the bearer credential; the backend reads the thread, repository and pull
request from the token, so nothing the model writes can point a call elsewhere.
"""

import os
from datetime import timedelta
from typing import Final

from langgraph.config import get_config
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from pydantic import JsonValue

RUNTIME_TOKEN_CONFIG_KEY: Final = "__open_swe_runtime_token__"
BACKEND_URL_ENV: Final = "OPEN_SWE_BACKEND_URL"
_TOOL_SERVER_PATH: Final = "/remote-runtime/mcp"
# The backend enforces its own deadlines; this only bounds a lost connection.
_RESPONSE_HEADROOM_SECONDS: Final = 30.0

PREPARE_HOOK: Final = "runtime__prepare_run"
REFRESH_HOOK: Final = "runtime__refresh_sandbox_credentials"
DRAIN_HOOK: Final = "runtime__drain_message_queue"
SETTLE_HOOK: Final = "runtime__settle_review_check"


class BackendCallError(RuntimeError):
    """The backend refused or could not answer a call; never carries the token."""


def _tool_server_url() -> str:
    base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
    if not base:
        raise BackendCallError(f"{BACKEND_URL_ENV} is not configured")
    return f"{base}{_TOOL_SERVER_PATH}"


def current_thread_id() -> str:
    thread_id = get_config().get("configurable", {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise BackendCallError("This run has no thread")
    return thread_id


def _run_token() -> str:
    token = get_config().get("configurable", {}).get(RUNTIME_TOKEN_CONFIG_KEY)
    if not isinstance(token, str) or not token:
        raise BackendCallError("This run carries no Open SWE run token; start it through Open SWE")
    return token


def _error_text(result: types.CallToolResult) -> str:
    texts = [block.text for block in result.content if isinstance(block, types.TextContent)]
    return "; ".join(text for text in texts if text) or "The backend refused the call"


async def call_backend(
    name: str, arguments: dict[str, JsonValue], *, timeout_seconds: float
) -> dict[str, JsonValue]:
    """Call one backend tool or run hook as this run and return its structured result."""
    wait = timeout_seconds + _RESPONSE_HEADROOM_SECONDS
    url = _tool_server_url()
    headers = {"Authorization": f"Bearer {_run_token()}"}
    try:
        async with (
            streamablehttp_client(url, headers=headers, timeout=wait, sse_read_timeout=wait) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            # Sent directly rather than through call_tool, which lists every tool on a
            # fresh session to look up output schemas these tools do not declare.
            result = await session.send_request(
                types.ClientRequest(
                    types.CallToolRequest(
                        params=types.CallToolRequestParams(name=name, arguments=arguments)
                    )
                ),
                types.CallToolResult,
                request_read_timeout_seconds=timedelta(seconds=wait),
            )
    except Exception as exc:
        # Named by type only: transport errors can echo the request, and it carries the token.
        raise BackendCallError(
            f"{name} could not reach the backend ({type(exc).__name__})"
        ) from exc
    if result.isError:
        raise BackendCallError(_error_text(result))
    if result.structuredContent is None:
        raise BackendCallError(f"{name} answered without a structured result")
    return dict(result.structuredContent)
