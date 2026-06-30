"""Native browser-automation tools backed by the Stagehand SDK.

We drive a real browser via Stagehand's Python SDK directly (no MCP
subprocess). Stagehand exposes high-level, model-driven primitives — ``act``,
``observe``, ``extract`` — on top of a managed browser session.

Two execution modes, selected by ``STAGEHAND_ENV`` (default ``LOCAL``):

* ``LOCAL``  — Stagehand runs its bundled local engine in-process and drives a
  local Chromium. Nothing leaves the host. Needs a Chrome/Chromium binary;
  point at it with ``STAGEHAND_LOCAL_CHROME_PATH`` if auto-detection fails.
* ``BROWSERBASE`` — the browser runs on Browserbase's cloud. Requires
  ``BROWSERBASE_API_KEY`` (and a project via ``BROWSERBASE_PROJECT_ID``).

Stagehand's ``act``/``observe``/``extract`` call an LLM. In ``BROWSERBASE``
mode the hosted Stagehand API ships with model support, so no model key is
required — provide one only to override the model. In ``LOCAL`` mode the engine
runs on the host, so a model key is required: ``STAGEHAND_MODEL_API_KEY``
(falls back to ``MODEL_API_KEY`` then ``ANTHROPIC_API_KEY``). Override the model
in either mode with ``STAGEHAND_MODEL`` (default ``anthropic/claude-sonnet-4-5``).

The browser tools are gated on having a usable config: a model key (LOCAL) or
Browserbase credentials (BROWSERBASE). When unconfigured the integration is a
no-op.

One browser session is kept per agent thread (keyed by ``thread_id`` from the
run config) and reused across tool calls, so ``navigate`` → ``act`` → ``extract``
operate on the same live page. Always finish with ``browser_close``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langgraph.config import get_config

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"

# thread_id -> (client, session)
_SESSIONS: dict[str, tuple[Any, Any]] = {}
_LOCK = asyncio.Lock()


def _is_local() -> bool:
    return os.getenv("STAGEHAND_ENV", "LOCAL").strip().upper() != "BROWSERBASE"


def _model_name() -> str:
    return os.getenv("STAGEHAND_MODEL", _DEFAULT_MODEL)


def _model_api_key() -> str | None:
    return (
        os.getenv("STAGEHAND_MODEL_API_KEY")
        or os.getenv("MODEL_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )


def _headless() -> bool:
    return os.getenv("STAGEHAND_HEADLESS", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def browser_tools_enabled() -> bool:
    """Whether the browser tools have enough configuration to run.

    LOCAL mode needs a model key (the engine runs on the host). BROWSERBASE
    mode only needs Browserbase credentials — the hosted Stagehand API ships
    with model support, so a model key is optional there.
    """
    if _is_local():
        return bool(_model_api_key())
    return bool(os.getenv("BROWSERBASE_API_KEY"))


def _thread_id() -> str:
    try:
        config = get_config()
    except Exception:  # noqa: BLE001 - outside a run (tests); fall back to a shared key
        return "default"
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return thread_id if isinstance(thread_id, str) and thread_id else "default"


def _build_client() -> Any:
    from stagehand import AsyncStagehand

    return AsyncStagehand(
        server="local" if _is_local() else "remote",
        browserbase_api_key=os.getenv("BROWSERBASE_API_KEY"),
        model_api_key=_model_api_key(),
        local_headless=_headless(),
        local_chrome_path=os.getenv("STAGEHAND_LOCAL_CHROME_PATH"),
    )


def _browser_spec() -> dict[str, Any]:
    """Build the ``browser`` argument for ``sessions.start``.

    Local sessions must provide ``launch_options`` (or a CDP URL); we pass
    headless + the Chrome executable path so the local engine launches it.
    """
    if not _is_local():
        return {"type": "browserbase"}
    launch_options: dict[str, Any] = {"headless": _headless()}
    chrome_path = os.getenv("STAGEHAND_LOCAL_CHROME_PATH")
    if chrome_path:
        launch_options["executable_path"] = chrome_path
    return {"type": "local", "launch_options": launch_options}


async def _get_session(create: bool = True) -> Any:
    """Return the live Stagehand session for this thread, creating one if needed."""
    thread_id = _thread_id()
    async with _LOCK:
        existing = _SESSIONS.get(thread_id)
        if existing is not None:
            return existing[1]
        if not create:
            return None
        client = _build_client()
        session = await client.sessions.start(
            model_name=_model_name(), browser=_browser_spec()
        )
        _SESSIONS[thread_id] = (client, session)
        logger.info("Started Stagehand session %s for thread %s", session.id, thread_id)
        return session


def _session_meta(session: Any) -> dict[str, Any]:
    data = getattr(session, "data", None)
    meta: dict[str, Any] = {"session_id": getattr(session, "id", None)}
    cdp_url = getattr(data, "cdp_url", None)
    if cdp_url:
        meta["cdp_url"] = cdp_url
    if not _is_local() and meta.get("session_id"):
        meta["replay_url"] = f"https://www.browserbase.com/sessions/{meta['session_id']}"
    return meta


async def browser_navigate(url: str) -> dict[str, Any]:
    """Open a browser (if not already open) and navigate to a URL.

    Starts a fresh browser session on first use within this task and reuses it
    for subsequent browser tool calls. Use this before ``browser_act``,
    ``browser_observe``, or ``browser_extract``.

    Args:
        url: The absolute URL to load (e.g. ``https://example.com``).

    Returns:
        ``{success, url, session_id, ...}`` on success, or ``{success: False,
        error}`` on failure.
    """
    try:
        session = await _get_session()
        await session.navigate(url=url)
        return {"success": True, "url": url, **_session_meta(session)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"browser_navigate failed: {e!s}"}


async def browser_act(action: str) -> dict[str, Any]:
    """Perform a single natural-language action on the current page.

    Examples: "click the Sign in button", "type 'hello' into the search box",
    "select 'United States' from the country dropdown". Keep each call to one
    discrete action and verify the result before the next step.

    Args:
        action: A concise, specific instruction describing one action.

    Returns:
        ``{success, result}`` on success, or ``{success: False, error}``.
    """
    try:
        session = await _get_session(create=False)
        if session is None:
            return {"success": False, "error": "No active browser. Call browser_navigate first."}
        result = await session.act(input=action)
        return {"success": True, "result": _unwrap_result(_to_jsonable(result))}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"browser_act failed: {e!s}"}


async def browser_observe(instruction: str) -> dict[str, Any]:
    """List actionable elements on the current page matching an instruction.

    Use this to discover what can be clicked/typed before calling
    ``browser_act`` on an unfamiliar page.

    Args:
        instruction: What to look for, e.g. "find the login form fields".

    Returns:
        ``{success, observations}`` on success, or ``{success: False, error}``.
    """
    try:
        session = await _get_session(create=False)
        if session is None:
            return {"success": False, "error": "No active browser. Call browser_navigate first."}
        result = await session.observe(instruction=instruction)
        return {"success": True, "observations": _unwrap_result(_to_jsonable(result))}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"browser_observe failed: {e!s}"}


async def browser_extract(
    instruction: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Extract structured data from the current page.

    Args:
        instruction: What to extract, e.g. "the title and price of each product".
        schema: Optional JSON Schema describing the desired shape of the result.
            When omitted, Stagehand returns its best-effort structured guess.

    Returns:
        ``{success, data}`` on success, or ``{success: False, error}``.
    """
    try:
        session = await _get_session(create=False)
        if session is None:
            return {"success": False, "error": "No active browser. Call browser_navigate first."}
        if schema is not None:
            result = await session.extract(instruction=instruction, schema=schema)
        else:
            result = await session.extract(instruction=instruction)
        return {"success": True, "data": _unwrap_result(_to_jsonable(result))}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"browser_extract failed: {e!s}"}


async def browser_close() -> dict[str, Any]:
    """Close the current browser session and release its resources.

    Call this when finished with browser work. Safe to call even if no session
    is open.
    """
    thread_id = _thread_id()
    async with _LOCK:
        entry = _SESSIONS.pop(thread_id, None)
    if entry is None:
        return {"success": True, "closed": False}
    client, session = entry
    try:
        await session.end()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to end Stagehand session cleanly", exc_info=True)
    try:
        await client.close()
    except Exception:  # noqa: BLE001
        logger.debug("Failed to close Stagehand client", exc_info=True)
    return {"success": True, "closed": True}


def _unwrap_result(value: Any) -> Any:
    """Peel Stagehand's ``{data: {result: ...}}`` envelope down to the payload."""
    cur = value
    for _ in range(3):
        if isinstance(cur, dict) and "result" in cur:
            return cur["result"]
        if isinstance(cur, dict) and isinstance(cur.get("data"), dict):
            cur = cur["data"]
            continue
        break
    return value


def _to_jsonable(result: Any) -> Any:
    """Best-effort conversion of Stagehand response models to plain data."""
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(result, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:  # noqa: BLE001
                pass
    data = getattr(result, "data", None)
    if data is not None and data is not result:
        return _to_jsonable(data)
    return result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result)


def load_browser_tools() -> list[Any]:
    """Return the Stagehand browser tools, or [] when unconfigured."""
    if not browser_tools_enabled():
        return []
    logger.info(
        "Stagehand browser tools enabled (mode=%s, model=%s)",
        "LOCAL" if _is_local() else "BROWSERBASE",
        _model_name(),
    )
    return [
        browser_navigate,
        browser_act,
        browser_observe,
        browser_extract,
        browser_close,
    ]
