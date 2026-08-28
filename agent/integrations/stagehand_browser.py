"""Stagehand browser tools executed inside the thread's task sandbox."""

import base64
import json
import logging
import os
import shlex
from typing import Any
from urllib.parse import urlparse

from langgraph.config import get_config

from agent.integrations.langsmith import get_async_sandbox_client
from agent.utils.sandbox_state import unwrap_sandbox_backend

from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"
_RUNTIME = "/opt/open-swe/stagehand_runtime.py"
_SOCKET = "/tmp/open-swe-stagehand.sock"


def _model_name() -> str:
    return os.getenv("STAGEHAND_MODEL", _DEFAULT_MODEL)


def _model_api_key() -> str | None:
    return (
        os.getenv("STAGEHAND_MODEL_API_KEY")
        or os.getenv("MODEL_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )


def _headless() -> bool:
    return os.getenv("STAGEHAND_HEADLESS", "true").strip().lower() not in ("0", "false", "no")


def browser_tools_enabled() -> bool:
    """Whether sandbox-local browser automation is configured."""
    provider = _model_name().split("/", 1)[0].split(":", 1)[0]
    return os.getenv("SANDBOX_TYPE", "langsmith") == "langsmith" and bool(
        _model_api_key() and provider in {"anthropic", "openai"}
    )


async def _proxy_url() -> str:
    response = await _request("proxy")
    if not response.get("success"):
        raise RuntimeError(response.get("error", "unable to start browser proxy"))
    port = response.get("port")
    if not isinstance(port, int):
        fallback_url = response.get("url")
        parsed = urlparse(fallback_url) if isinstance(fallback_url, str) else None
        if parsed is None or parsed.port is None:
            raise RuntimeError("browser runtime returned an invalid proxy port")
        return f"{parsed.scheme}://{parsed.netloc}"
    backend_proxy = await get_sandbox_backend(_thread_id())
    backend = unwrap_sandbox_backend(backend_proxy)
    async with get_async_sandbox_client() as client:
        service = await client.service(backend.id, port, expires_in_seconds=600)
    if not service.browser_url:
        raise RuntimeError("LangSmith did not return a service URL")
    return service.browser_url


def _thread_id() -> str:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise RuntimeError("no thread_id in run config")
    return thread_id


async def _request(operation: str, **payload: Any) -> dict[str, Any]:
    backend = await get_sandbox_backend(_thread_id())
    if operation == "navigate" and "proxy_url" not in payload:
        try:
            payload["proxy_url"] = await _proxy_url()
        except Exception as exc:
            return {
                "success": False,
                "error": f"browser automation is unavailable in this sandbox: {exc}",
            }
    request = base64.urlsafe_b64encode(
        json.dumps(
            {
                "operation": operation,
                "model_name": _model_name(),
                "headless": _headless(),
                **payload,
            }
        ).encode()
    ).decode()
    runtime = shlex.quote(_RUNTIME)
    socket = shlex.quote(_SOCKET)
    encoded = shlex.quote(request)
    health = base64.urlsafe_b64encode(b'{"operation":"health"}').decode()
    command = (
        f"python {runtime} request {socket} {health} >/dev/null 2>&1 || {{ "
        f"rm -f {socket}; "
        f"setsid python {runtime} serve {socket} >/tmp/open-swe-stagehand.log 2>&1 </dev/null & "
        f"for i in $(seq 1 100); do "
        f"python {runtime} request {socket} {health} >/dev/null 2>&1 && break; sleep .1; "
        f"done; }}; "
        f"python {runtime} request {socket} {encoded}"
    )
    result = await backend.aexecute(command, timeout=180)
    if result.exit_code != 0:
        detail = result.output.strip() or f"sandbox command exited {result.exit_code}"
        return {"success": False, "error": f"browser_{operation} failed: {detail}"}
    try:
        response = json.loads(result.output)
    except json.JSONDecodeError:
        return {"success": False, "error": f"browser_{operation} returned an invalid response"}
    return (
        response if isinstance(response, dict) else {"success": False, "error": "invalid response"}
    )


async def browser_navigate(url: str) -> dict[str, Any]:
    """Open sandbox-local Chromium and navigate to a URL, including localhost."""
    return await _request("navigate", url=url)


async def browser_act(action: str) -> dict[str, Any]:
    """Perform one natural-language action on the current page."""
    return await _request("act", action=action)


async def browser_observe(instruction: str) -> dict[str, Any]:
    """List actionable elements on the current page matching an instruction."""
    return await _request("observe", instruction=instruction)


async def browser_extract(instruction: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract structured data from the current page."""
    return await _request("extract", instruction=instruction, schema=schema)


async def browser_close() -> dict[str, Any]:
    """Close the sandbox-local browser session."""
    return await _request("close")


async def load_browser_tools() -> list[Any]:
    """Return sandbox-local Stagehand tools when securely configured."""
    if not browser_tools_enabled():
        return []
    health = await _request("health")
    if not health.get("success"):
        return []
    logger.info("Sandbox-local Stagehand tools enabled (model=%s)", _model_name())
    return [browser_navigate, browser_act, browser_observe, browser_extract, browser_close]
