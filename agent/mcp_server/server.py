"""A small, stateless MCP server (Streamable HTTP) exposing PR review tools.

Mounted at ``POST /integrations/mcp`` (override with ``MCP_ROUTE_PATH``). It speaks just enough of the protocol for tool use:
``initialize``, ``ping``, ``tools/list`` and ``tools/call``. There are no
sessions and no server-initiated messages, so GET/DELETE return 405, which the
Streamable HTTP spec permits.

``request_review`` can take minutes. When the client accepts
``text/event-stream`` the call is answered as an SSE stream that emits progress
notifications (or comment keepalives) so proxies don't drop the connection.
Disconnecting never cancels the review itself; poll ``get_review`` instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent.mcp_server import reviews
from agent.mcp_server.auth import AuthError, Caller, verify_token

logger = logging.getLogger(__name__)

# LangGraph Platform serves its own /mcp (graphs as assistants), so default elsewhere.
MCP_ROUTE_PATH = os.environ.get("MCP_ROUTE_PATH", "/integrations/mcp")

router = APIRouter()

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
KEEPALIVE_SECONDS = 10.0
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 1800

TOOLS: list[dict[str, Any]] = [
    {
        "name": "request_review",
        "description": (
            "Ask Open SWE to review a GitHub pull request and return structured findings. "
            "With wait=true (default) this blocks until the review finishes or the timeout "
            "elapses. With wait=false it returns immediately with a thread_id; call "
            "get_review to collect the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_url": {
                    "type": "string",
                    "description": "https://github.com/<owner>/<repo>/pull/<number>",
                },
                "wait": {"type": "boolean", "default": True},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                    "description": "Only used when wait=true. On timeout the review keeps "
                    "running and the result has status 'running'.",
                },
            },
            "required": ["pr_url"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Request PR review", "readOnlyHint": False, "openWorldHint": True},
    },
    {
        "name": "get_review",
        "description": "Get the status and findings of a review started with request_review.",
        "inputSchema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string", "format": "uuid"}},
            "required": ["thread_id"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Get PR review", "readOnlyHint": True},
    },
]
_TOOL_NAMES = {t["name"] for t in TOOLS}


# --- JSON-RPC helpers -------------------------------------------------------


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_ok(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}],
        "structuredContent": data,
        "isError": False,
    }


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"{code}: {message}"}],
        "structuredContent": {"error": code, "message": message},
        "isError": True,
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"event: message\ndata: {json.dumps(payload, default=str)}\n\n"


# --- tools ------------------------------------------------------------------


async def _request_review(args: dict[str, Any], caller: Caller) -> dict[str, Any]:
    pr_url = args.get("pr_url")
    if not isinstance(pr_url, str):
        raise reviews.ReviewError("invalid_arguments", "pr_url is required")
    wait = args.get("wait", True)
    timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(wait, bool) or not isinstance(timeout, int) or isinstance(timeout, bool):
        raise reviews.ReviewError("invalid_arguments", "wait must be boolean, timeout_seconds integer")
    timeout = max(10, min(timeout, MAX_TIMEOUT_SECONDS))

    ref = reviews.parse_pr_url(pr_url)
    reviews.assert_repo_allowed(ref)

    client = reviews.get_langgraph_client()
    handle = await reviews.start_review(client, caller, ref)
    run_status = "running"
    if wait:
        run_status = await reviews.wait_for_run(
            client, handle.thread_id, handle.run_id, timeout=timeout
        )
    result = await reviews.build_result(
        client, handle.thread_id, handle.run_id, handle.web_url, run_status
    )
    if handle.joined_existing_run:
        result["note"] = "A review of this PR was already in progress; attached to it."
    return result


async def _get_review(args: dict[str, Any], caller: Caller) -> dict[str, Any]:
    raw_id = args.get("thread_id")
    try:
        thread_id = str(uuid.UUID(str(raw_id)))
    except ValueError as exc:
        raise reviews.ReviewError("invalid_arguments", "thread_id must be a UUID") from exc
    client = reviews.get_langgraph_client()
    await reviews.assert_owns_thread(client, caller, thread_id)
    run = await reviews.latest_run(client, thread_id)
    return await reviews.build_result(
        client, thread_id, run["run_id"], reviews.web_url_for(thread_id), run["status"]
    )


async def _call_tool(name: str, args: dict[str, Any], caller: Caller) -> dict[str, Any]:
    """Run a tool and always return a CallToolResult; never raises."""
    try:
        handler = _request_review if name == "request_review" else _get_review
        return _tool_ok(await handler(args, caller))
    except reviews.ReviewError as exc:
        return _tool_error(exc.code, exc.message)
    except Exception:
        logger.exception("mcp tool %s failed", name)
        return _tool_error("internal_error", "The review service failed. Try again shortly.")


async def _stream_call(
    request_id: Any, progress_token: Any, name: str, args: dict[str, Any], caller: Caller
) -> AsyncIterator[str]:
    task = asyncio.ensure_future(_call_tool(name, args, caller))
    elapsed = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_SECONDS)
            if done:
                break
            elapsed += KEEPALIVE_SECONDS
            if progress_token is not None:
                yield _sse(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {
                            "progressToken": progress_token,
                            "progress": elapsed,
                            "message": f"Review in progress ({int(elapsed)}s)",
                        },
                    }
                )
            else:
                yield ": keepalive\n\n"
        yield _sse(_result(request_id, task.result()))
    finally:
        # Client went away: stop polling. The review run itself keeps going.
        if not task.done():
            task.cancel()


# --- transport --------------------------------------------------------------


def is_enabled() -> bool:
    on = os.environ.get("MCP_SERVER_ENABLED", "").lower() in {"1", "true", "yes"}
    return on and len(os.environ.get("MCP_TOKEN_SECRET", "")) >= 32


def _check_origin(request: Request) -> None:
    """Reject browser-originated requests (DNS-rebinding protection per the MCP spec)."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {o.strip().rstrip("/") for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",")}
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=403, detail="Origin not allowed")


def _authenticate(request: Request) -> Caller:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise AuthError("missing bearer token")
        return verify_token(token.strip())
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="open-swe-mcp"'},
        ) from exc


@router.get(MCP_ROUTE_PATH, include_in_schema=False)
@router.delete(MCP_ROUTE_PATH, include_in_schema=False)
async def mcp_unsupported_method() -> Response:
    if not is_enabled():
        raise HTTPException(status_code=404)
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post(MCP_ROUTE_PATH, include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    if not is_enabled():
        raise HTTPException(status_code=404)
    _check_origin(request)
    caller = _authenticate(request)

    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return JSONResponse(_error(None, -32600, "Invalid request"), status_code=400)

    method = message.get("method")
    request_id = message.get("id")
    # Notifications (no id) and client responses (no method) get 202 and no body.
    if method is None or request_id is None:
        return Response(status_code=202)

    params = message.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(_error(request_id, -32602, "params must be an object"))

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return JSONResponse(
            _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "open-swe", "title": "Open SWE", "version": "0.1.0"},
                    "instructions": "Use request_review to have Open SWE review a GitHub PR.",
                },
            )
        )
    if method == "ping":
        return JSONResponse(_result(request_id, {}))
    if method == "tools/list":
        return JSONResponse(_result(request_id, {"tools": TOOLS}))
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in _TOOL_NAMES or not isinstance(args, dict):
            return JSONResponse(_error(request_id, -32602, f"Unknown tool or bad arguments: {name}"))
        wants_sse = "text/event-stream" in request.headers.get("accept", "")
        if name == "request_review" and args.get("wait", True) is True and wants_sse:
            token = (params.get("_meta") or {}).get("progressToken")
            return StreamingResponse(
                _stream_call(request_id, token, name, args, caller),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return JSONResponse(_result(request_id, await _call_tool(name, args, caller)))
    return JSONResponse(_error(request_id, -32601, f"Method not found: {method}"))
