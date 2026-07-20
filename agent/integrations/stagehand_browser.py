"""Native browser-automation tools backed by the Stagehand SDK.

We drive a real browser via Stagehand's Python SDK directly (no MCP
subprocess). Stagehand exposes high-level, model-driven primitives — ``act``,
``observe``, ``extract`` — on top of a managed browser session.

Two execution modes, selected by ``STAGEHAND_ENV`` (default ``LOCAL``):

* ``LOCAL``  — Stagehand runs its bundled local engine in-process and drives a
  local Chromium. Nothing leaves the host. Needs a Chrome/Chromium binary;
  point at it with ``STAGEHAND_LOCAL_CHROME_PATH`` if auto-detection fails.
* ``BROWSERBASE`` — the browser runs on Browserbase's cloud. Requires
  ``BROWSERBASE_API_KEY``. ``BROWSERBASE_PROJECT_ID`` is forwarded when set.

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
import contextlib
import inspect
import json
import logging
import os
from typing import Any

from langgraph.config import get_config

from ..utils.url_safety import is_url_safe

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"

# thread_id -> (client, session)
_SESSIONS: dict[str, tuple[Any, Any]] = {}
_LOCK = asyncio.Lock()


class BrowserNavigationBlocked(RuntimeError):
    pass


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
        browserbase_project_id=os.getenv("BROWSERBASE_PROJECT_ID"),
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


def _browserbase_session_create_params() -> dict[str, Any]:
    if _is_local():
        return {}
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")
    return {"project_id": project_id} if project_id else {}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:  # noqa: BLE001
        return None


def _callable_attr(obj: Any, name: str) -> Any:
    value = _safe_attr(obj, name)
    return value if callable(value) else None


def _request_url(route: Any, request: Any | None = None) -> str | None:
    request = request or _safe_attr(route, "request")
    if callable(request):
        request = request()
    url = _safe_attr(request, "url") if request is not None else None
    if isinstance(url, str) and url:
        return url
    url = _safe_attr(route, "url")
    return url if isinstance(url, str) and url else None


async def _continue_route(route: Any) -> None:
    continue_ = _callable_attr(route, "continue_") or _callable_attr(route, "fallback")
    if continue_ is not None:
        await _maybe_await(continue_())


async def _abort_route(route: Any) -> None:
    abort = _callable_attr(route, "abort")
    if abort is not None:
        await _maybe_await(abort())


def _mark_blocked_request(session: Any, url: str, reason: str) -> None:
    try:
        session._open_swe_blocked_request = (url, reason)
    except Exception:  # noqa: BLE001
        pass


def _clear_blocked_request(session: Any) -> None:
    try:
        if hasattr(session, "_open_swe_blocked_request"):
            delattr(session, "_open_swe_blocked_request")
    except Exception:  # noqa: BLE001
        pass


def _blocked_request_error(session: Any) -> str | None:
    blocked = _safe_attr(session, "_open_swe_blocked_request")
    if isinstance(blocked, tuple) and len(blocked) == 2:
        url, reason = blocked
        return f"blocked browser request to {url}: {reason}"
    return None


def _set_current_page_url(session: Any, url: str) -> None:
    if not url or url == "about:blank":
        return
    try:
        session._open_swe_current_page_url = url
    except Exception:  # noqa: BLE001
        pass


def _cdp_url(session: Any) -> str | None:
    url = _safe_attr(_safe_attr(session, "data"), "cdp_url")
    return url if isinstance(url, str) and url else None


async def _connect_cdp_websocket(cdp_url: str) -> Any:
    import websockets

    return await websockets.connect(cdp_url)


class _CDPBrowserURLGuard:
    def __init__(self, session: Any, cdp_url: str) -> None:
        self._session = session
        self._cdp_url = cdp_url
        self._ws: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._attached_sessions: set[str] = set()
        self._protected_target_ids: set[str] = set()
        self._configuration_tasks: set[asyncio.Task[None]] = set()
        self._fetch_tasks: dict[str, asyncio.Task[None]] = {}
        self._failure: BaseException | None = None

    async def start(self) -> None:
        try:
            self._ws = await _connect_cdp_websocket(self._cdp_url)
            self._task = asyncio.create_task(self._run())
            await self._send(
                "Target.setAutoAttach",
                {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": True,
                    "flatten": True,
                },
            )
            await self._send("Target.setDiscoverTargets", {"discover": True})
            await self._protect_initial_targets()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        for task in self._configuration_tasks:
            task.cancel()
        for task in self._fetch_tasks.values():
            task.cancel()
        if self._configuration_tasks:
            await asyncio.gather(*self._configuration_tasks, return_exceptions=True)
        if self._fetch_tasks:
            await asyncio.gather(*self._fetch_tasks.values(), return_exceptions=True)
        self._configuration_tasks.clear()
        self._fetch_tasks.clear()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._ws is not None:
            close = _callable_attr(self._ws, "close")
            if close is not None:
                await _maybe_await(close())
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def _send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> Any:
        if self._ws is None:
            return None
        self._next_id += 1
        message_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[message_id] = future
        await self._send_message(message_id, method, params, session_id=session_id)
        try:
            return await asyncio.wait_for(future, timeout=10)
        finally:
            self._pending.pop(message_id, None)

    async def _send_fire_and_forget(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        if self._ws is None:
            return
        self._next_id += 1
        await self._send_message(self._next_id, method, params, session_id=session_id)

    async def _send_message(
        self,
        message_id: int,
        method: str,
        params: dict[str, Any] | None,
        *,
        session_id: str | None,
    ) -> None:
        websocket = self._ws
        if websocket is None:
            raise RuntimeError("CDP websocket is not connected")
        message: dict[str, Any] = {"id": message_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id is not None:
            message["sessionId"] = session_id
        async with self._send_lock:
            await websocket.send(json.dumps(message))

    async def _run(self) -> None:
        assert self._ws is not None
        try:
            async for raw_message in self._ws:
                try:
                    message = json.loads(raw_message)
                except Exception:  # noqa: BLE001
                    logger.debug("Ignoring malformed CDP message", exc_info=True)
                    continue
                message_id = message.get("id")
                if isinstance(message_id, int):
                    future = self._pending.pop(message_id, None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(str(message["error"])))
                        else:
                            future.set_result(message.get("result"))
                    continue
                await self._handle_event(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._failure = e
            logger.debug("CDP browser URL guard stopped", exc_info=True)
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(e)

    async def _handle_event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, dict):
            return
        if method == "Target.attachedToTarget":
            session_id = params.get("sessionId")
            target_info = params.get("targetInfo")
            if isinstance(session_id, str) and isinstance(target_info, dict):
                url = target_info.get("url")
                if isinstance(url, str):
                    _set_current_page_url(self._session, url)
                target_type = target_info.get("type")
                waiting_for_debugger = params.get("waitingForDebugger") is True
                if target_type in {"page", "iframe", "webview"} or waiting_for_debugger:
                    self._schedule_target_configuration(
                        session_id,
                        target_info,
                        waiting_for_debugger=waiting_for_debugger,
                    )
            return
        if method == "Target.targetInfoChanged":
            target_info = params.get("targetInfo")
            if isinstance(target_info, dict):
                url = target_info.get("url")
                if isinstance(url, str):
                    _set_current_page_url(self._session, url)
            return
        if method == "Page.frameNavigated":
            frame = params.get("frame")
            if isinstance(frame, dict):
                url = frame.get("url")
                if isinstance(url, str):
                    _set_current_page_url(self._session, url)
            return
        if method == "Fetch.requestPaused":
            session_id = message.get("sessionId")
            if isinstance(session_id, str):
                await self._handle_paused_request(session_id, params)

    async def _protect_initial_targets(self) -> None:
        targets = await self._target_infos()
        for target_info in targets:
            try:
                await self._attach_to_target(target_info)
            except Exception:  # noqa: BLE001
                logger.debug("Initial CDP target disappeared before attachment", exc_info=True)
        await self._wait_for_configuration_tasks()
        self._raise_if_failed()

        current_targets = await self._target_infos()
        for target_info in current_targets:
            target_id = target_info.get("targetId")
            if not isinstance(target_id, str) or target_id in self._protected_target_ids:
                continue
            try:
                await self._attach_to_target(target_info)
            except Exception:  # noqa: BLE001
                logger.debug("CDP target could not be protected", exc_info=True)
        await self._wait_for_configuration_tasks()
        self._raise_if_failed()

        unprotected = {
            target_info["targetId"]
            for target_info in current_targets
            if isinstance(target_info.get("targetId"), str)
            and target_info["targetId"] not in self._protected_target_ids
        }
        if not self._attached_sessions or unprotected:
            raise RuntimeError("CDP URL guard did not protect every live browser target")

    async def _target_infos(self) -> list[dict[str, Any]]:
        targets = await self._send("Target.getTargets")
        if not isinstance(targets, dict):
            return []
        return [
            target_info
            for target_info in targets.get("targetInfos", [])
            if isinstance(target_info, dict)
            and target_info.get("type") in {"page", "iframe", "webview"}
        ]

    async def _attach_to_target(self, target_info: dict[str, Any]) -> None:
        target_type = target_info.get("type")
        target_id = target_info.get("targetId")
        url = target_info.get("url")
        if isinstance(url, str):
            _set_current_page_url(self._session, url)
        if target_type not in {"page", "iframe", "webview"} or not isinstance(target_id, str):
            return
        result = await self._send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result.get("sessionId") if isinstance(result, dict) else None
        if isinstance(session_id, str):
            await self._enable_fetch(session_id)
            self._protected_target_ids.add(target_id)

    async def _enable_fetch(self, session_id: str) -> None:
        if session_id in self._attached_sessions:
            return
        task = self._fetch_tasks.get(session_id)
        if task is None:
            task = asyncio.create_task(self._enable_fetch_commands(session_id))
            self._fetch_tasks[session_id] = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done():
                self._fetch_tasks.pop(session_id, None)

    async def _enable_fetch_commands(self, session_id: str) -> None:
        await self._send("Page.enable", session_id=session_id)
        await self._send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*"}]},
            session_id=session_id,
        )
        self._attached_sessions.add(session_id)

    def _schedule_target_configuration(
        self,
        session_id: str,
        target_info: dict[str, Any],
        *,
        waiting_for_debugger: bool,
    ) -> None:
        task = asyncio.create_task(
            self._configure_attached_target(
                session_id,
                target_info,
                waiting_for_debugger=waiting_for_debugger,
            )
        )
        self._configuration_tasks.add(task)
        task.add_done_callback(self._configuration_finished)

    async def _configure_attached_target(
        self,
        session_id: str,
        target_info: dict[str, Any],
        *,
        waiting_for_debugger: bool,
    ) -> None:
        if target_info.get("type") in {"page", "iframe", "webview"}:
            await self._enable_fetch(session_id)
            target_id = target_info.get("targetId")
            if isinstance(target_id, str):
                self._protected_target_ids.add(target_id)
        if waiting_for_debugger:
            await self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)

    def _configuration_finished(self, task: asyncio.Task[None]) -> None:
        self._configuration_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._failure = error

    async def _wait_for_configuration_tasks(self) -> None:
        while self._configuration_tasks:
            await asyncio.gather(*tuple(self._configuration_tasks), return_exceptions=True)

    def _raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError("CDP URL guard failed") from self._failure

    def failure(self) -> BaseException | None:
        if self._failure is not None:
            return self._failure
        if self._task is not None and self._task.done() and not self._task.cancelled():
            return self._task.exception() or RuntimeError("CDP URL guard stopped")
        return None

    async def _handle_paused_request(self, session_id: str, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        request = params.get("request")
        url = request.get("url") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not isinstance(url, str):
            return
        safe, reason = is_url_safe(url)
        if safe:
            await self._send_fire_and_forget(
                "Fetch.continueRequest",
                {"requestId": request_id},
                session_id=session_id,
            )
            return
        _mark_blocked_request(self._session, url, reason)
        logger.warning("Blocked unsafe browser request to %s: %s", url, reason)
        await self._send_fire_and_forget(
            "Fetch.failRequest",
            {"requestId": request_id, "errorReason": "Aborted"},
            session_id=session_id,
        )


def _guard_targets(session: Any) -> list[Any]:
    targets: list[Any] = []
    for candidate in (
        session,
        _safe_attr(session, "page"),
        _safe_attr(session, "context"),
        _safe_attr(session, "browser_context"),
        _safe_attr(_safe_attr(session, "data"), "page"),
        _safe_attr(_safe_attr(session, "data"), "context"),
        _safe_attr(_safe_attr(session, "data"), "browser_context"),
    ):
        if candidate is None or any(candidate is target for target in targets):
            continue
        targets.append(candidate)
    return targets


async def _install_browser_url_guard(session: Any) -> None:
    installed = False
    cdp_url = _cdp_url(session)
    if cdp_url is not None and not _safe_attr(session, "_open_swe_cdp_guard"):
        cdp_guard: _CDPBrowserURLGuard | None = None
        try:
            cdp_guard = _CDPBrowserURLGuard(session, cdp_url)
            await cdp_guard.start()
            session._open_swe_cdp_guard = cdp_guard
            installed = True
        except Exception:  # noqa: BLE001
            if cdp_guard is not None:
                await cdp_guard.close()
            logger.warning("Failed to install CDP browser URL guard", exc_info=True)

    async def guarded_route(route: Any, request: Any | None = None) -> None:
        url = _request_url(route, request)
        if not url:
            await _continue_route(route)
            return
        safe, reason = is_url_safe(url)
        if safe:
            await _continue_route(route)
            return
        _mark_blocked_request(session, url, reason)
        logger.warning("Blocked unsafe browser request to %s: %s", url, reason)
        await _abort_route(route)

    for target in _guard_targets(session):
        if _safe_attr(target, "_open_swe_url_guard_installed"):
            installed = True
            continue
        route = _callable_attr(target, "route")
        if route is None:
            continue
        try:
            await _maybe_await(route("**/*", guarded_route))
            target._open_swe_url_guard_installed = True
            installed = True
        except Exception:  # noqa: BLE001
            logger.debug("Failed to install browser URL guard on %r", target, exc_info=True)
    if not installed:
        raise BrowserNavigationBlocked(
            "Stagehand session does not expose a working browser request hook for URL guarding"
        )


def _current_page_url(session: Any) -> str | None:
    cdp_url = _safe_attr(session, "_open_swe_current_page_url")
    if isinstance(cdp_url, str) and cdp_url and cdp_url != "about:blank":
        return cdp_url
    for target in _guard_targets(session):
        url = _safe_attr(target, "url")
        if isinstance(url, str) and url and url != "about:blank":
            return url
    return None


async def _raise_if_browser_blocked(session: Any, operation: str) -> None:
    cdp_guard = _safe_attr(session, "_open_swe_cdp_guard")
    guard_failure = cdp_guard.failure() if isinstance(cdp_guard, _CDPBrowserURLGuard) else None
    if guard_failure is not None:
        await browser_close()
        raise BrowserNavigationBlocked(f"{operation} blocked: browser URL guard failed")

    blocked_error = _blocked_request_error(session)
    if blocked_error:
        await browser_close()
        raise BrowserNavigationBlocked(f"{operation} blocked: {blocked_error}")

    current_url = _current_page_url(session)
    if not current_url:
        return
    safe, reason = is_url_safe(current_url)
    if not safe:
        await browser_close()
        raise BrowserNavigationBlocked(f"{operation} blocked after navigation: {reason}")


async def _prepare_browser_operation(session: Any, operation: str) -> None:
    await _raise_if_browser_blocked(session, operation)
    _clear_blocked_request(session)


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
        session_kwargs: dict[str, Any] = {"model_name": _model_name(), "browser": _browser_spec()}
        browserbase_session_create_params = _browserbase_session_create_params()
        if browserbase_session_create_params:
            session_kwargs["browserbase_session_create_params"] = browserbase_session_create_params
        session = await client.sessions.start(**session_kwargs)
        try:
            await _install_browser_url_guard(session)
        except BaseException:
            with contextlib.suppress(Exception):
                await session.end()
            with contextlib.suppress(Exception):
                await client.close()
            raise
        _SESSIONS[thread_id] = (client, session)
        logger.info("Started Stagehand session %s for thread %s", session.id, thread_id)
        return session


def _session_meta(session: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"session_id": getattr(session, "id", None)}
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
        safe, reason = is_url_safe(url)
        if not safe:
            return {"success": False, "error": f"browser_navigate blocked: {reason}"}
        session = await _get_session()
        await _prepare_browser_operation(session, "browser_navigate")
        await session.navigate(url=url)
        await _raise_if_browser_blocked(session, "browser_navigate")
        return {"success": True, "url": url, **_session_meta(session)}
    except BrowserNavigationBlocked as e:
        return {"success": False, "error": str(e)}
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
        await _prepare_browser_operation(session, "browser_act")
        result = await session.act(input=action)
        await _raise_if_browser_blocked(session, "browser_act")
        return {"success": True, "result": _unwrap_result(_to_jsonable(result))}
    except BrowserNavigationBlocked as e:
        return {"success": False, "error": str(e)}
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
        await _prepare_browser_operation(session, "browser_observe")
        result = await session.observe(instruction=instruction)
        await _raise_if_browser_blocked(session, "browser_observe")
        return {"success": True, "observations": _unwrap_result(_to_jsonable(result))}
    except BrowserNavigationBlocked as e:
        return {"success": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"browser_observe failed: {e!s}"}


async def browser_extract(instruction: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
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
        await _prepare_browser_operation(session, "browser_extract")
        if schema is not None:
            result = await session.extract(instruction=instruction, schema=schema)
        else:
            result = await session.extract(instruction=instruction)
        await _raise_if_browser_blocked(session, "browser_extract")
        return {"success": True, "data": _unwrap_result(_to_jsonable(result))}
    except BrowserNavigationBlocked as e:
        return {"success": False, "error": str(e)}
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
    cdp_guard = _safe_attr(session, "_open_swe_cdp_guard")
    close_cdp_guard = _callable_attr(cdp_guard, "close") if cdp_guard is not None else None
    if close_cdp_guard is not None:
        await _maybe_await(close_cdp_guard())
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
    return (
        result
        if isinstance(result, (dict, list, str, int, float, bool, type(None)))
        else str(result)
    )


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
