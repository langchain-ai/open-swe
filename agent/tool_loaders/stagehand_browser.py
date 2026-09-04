"""Stagehand browser tools executed inside the thread's task sandbox."""

import base64
import json
import logging
import shlex
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from agent.config import ENV
from agent.run_config import RunConfig
from agent.sandboxes.state import get_sandbox_backend

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"
_RUNTIME = "/opt/open-swe/stagehand_runtime.py"
_SOCKET = "/tmp/open-swe-stagehand.sock"


def _model_name() -> str:
    return ENV.STAGEHAND_MODEL.get(_DEFAULT_MODEL)


def _model_api_key() -> str | None:
    return (
        ENV.STAGEHAND_MODEL_API_KEY.optional()
        or ENV.MODEL_API_KEY.optional()
        or ENV.ANTHROPIC_API_KEY.optional()
    )


def _headless() -> bool:
    return ENV.STAGEHAND_HEADLESS.get().strip().lower() not in ("0", "false", "no")


def browser_tools_enabled() -> bool:
    """Whether sandbox-local browser automation is configured."""
    provider = _model_name().split("/", 1)[0].split(":", 1)[0]
    return ENV.SANDBOX_TYPE.get() == "langsmith" and bool(
        _model_api_key() and provider in {"anthropic", "openai"}
    )


def _thread_id() -> str:
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise RuntimeError("no thread_id in run config")
    return thread_id


async def _request(operation: str, **payload: Any) -> dict[str, Any]:
    backend = await get_sandbox_backend(_thread_id())
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


def load_browser_tools() -> list[BaseTool]:
    """Return sandbox-local Stagehand tools when securely configured."""
    if not browser_tools_enabled():
        return []
    logger.info("Sandbox-local Stagehand tools enabled (model=%s)", _model_name())
    return [
        StructuredTool.from_function(coroutine=tool)
        for tool in (
            browser_navigate,
            browser_act,
            browser_observe,
            browser_extract,
            browser_close,
        )
    ]
