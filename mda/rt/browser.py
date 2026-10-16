"""Browser use for the agent: Stagehand v4's tools on a LangSmith browser.

Ported from Managed Deep Agents' ``BrowserMiddleware``
(langchain-ai/managed-deepagents-sdk, ``managed_deepagents/browser.py``, PR #800),
which is not released yet. Keep the two in step until this runtime can declare
the browser with ``define_browser`` instead.

Each thread gets its own LangSmith browser, a sandbox separate from the thread's
code sandbox. The Stagehand client lives in this process and drives a window of
that browser through the LangSmith API's CDP proxy (the window's
``api_cdp_url``). The client is released after every run; the browser, its tabs
and its logins stay until it idles out.

Stagehand's engine is a Chrome extension. browserd loads extensions from its
extensions directory when Chrome starts, so the first open puts Stagehand's
there and restarts Chrome. The extension then dials Chrome from inside the
browser sandbox, on the window's own port.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlencode

import httpx2
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain.tools import BaseTool, ToolRuntime, tool
from langgraph.config import get_config
from langgraph.runtime import Runtime

#: The client and the browser's extension must be the same release; the pin in
#: ``pyproject.toml`` matches.
STAGEHAND_VERSION: Final = "4.2.1"

RUN_TIMEOUT_MS: Final = 60_000
MAX_QUALITY: Final = 100

_EXTENSIONS_DIR: Final = "/var/lib/browserd/extensions"
_EXTENSION_DIR: Final = f"{_EXTENSIONS_DIR}/stagehand-{STAGEHAND_VERSION}"
#: browserd's windows, each with the port it serves inside the browser sandbox.
_WINDOWS_FILE: Final = "/var/lib/browserd/attachments.json"
_WINDOW_NAME: Final = "open-swe"
_HTTP_TIMEOUT: Final = httpx2.Timeout(90.0)  # creating a browser waits for Chrome
_SETUP_TIMEOUT_S: Final = 300
_SECRET_QUERY: Final = re.compile(r"(api_key|token)=[^&\s'\"]+")

# Copies the extension from the same Stagehand release as the client; prints
# "installed" when it was not there yet. Nothing in it comes from the model.
_INSTALL_EXTENSION: Final = (
    f"test -f {_EXTENSION_DIR}/manifest.json || "
    "(python3 -m uv pip install --quiet --python python3 --no-deps "
    f"--target /tmp/open-swe-stagehand stagehand=={STAGEHAND_VERSION} && "
    f"mkdir -p {_EXTENSIONS_DIR} && "
    f"cp -r /tmp/open-swe-stagehand/stagehand/_extension {_EXTENSION_DIR} && echo installed)"
)

# A transport that died: reopen and try once more. Anything else (a page that
# threw, a bad selector) goes back to the model with the session left alone.
_DEAD_SESSION: Final = (
    "already initialized",
    "closed",
    "connection",
    "websocket",
    "window is gone",
)

# Descriptions are Stagehand's facade contract verbatim
# (`packages/integrations/core/src/facade/contract.ts`); do not reword them.
RUN_DESCRIPTION: Final = (
    "Browse and automate websites in the persistent browser by executing JavaScript "
    "against a Playwright-shaped API. The code runs inside an async function with "
    "page, context, and browser in scope (Playwright Page, BrowserContext, and "
    "Browser); use await directly and return a JSON-serializable value when useful. "
    'Navigate with await page.goto("https://example.com"); there is no separate '
    "navigate or start tool. Alternatively, provide a batch of actions using IDs from "
    "the latest snapshot. Provide exactly one of code or actions. Each action must use "
    '"op" (never "kind") and "id" (never "ref"). Copy the bracketed snapshot ID as a '
    'string. Examples: {"actions":[{"op":"click","id":"1-42"}]}, '
    '{"actions":[{"op":"fill","id":"2-14","value":"Miami"}]}, '
    '{"actions":[{"op":"select","id":"3-9","values":"Lowest price"}]}.'
)
SNAPSHOT_DESCRIPTION: Final = (
    "Capture the active page's Stagehand accessibility tree and hydrate its displayed "
    "IDs for subsequent run actions. Every call replaces the active page's ID map."
)
SCREENSHOT_DESCRIPTION: Final = (
    "Capture a screenshot of the active page. For size-constrained MCP clients, prefer "
    'a viewport JPEG: {"type":"jpeg","quality":40,"fullPage":false}.'
)

# `experimental_batch` hands the callback a worker-local batch context; the
# async IIFE makes a top-level `return` legal in model code.
_RUN_SOURCE: Final = """async (batch, input) => {
  const page = batch.page;
  const context = batch.context;
  return await (async () => { %s })();
}"""

# Snapshot actions in one round trip, as Stagehand's Deep Agents integration runs them.
_ACTIONS_SOURCE: Final = """async (batch, input) => {
  for (const step of input.steps) {
    const target = batch.page.locator(step.selector);
    if (step.op === "click") await target.click();
    else if (step.op === "hover") await target.hover();
    else if (step.op === "fill") await target.fill(step.value);
    else if (step.op === "type") await target.type(step.value);
    else if (step.op === "press") { await target.click(); await batch.page.keyPress(step.value); }
    else if (step.op === "select") await target.selectOption(step.value);
  }
  return { completed: input.steps.length, url: await batch.page.url() };
}"""

# Each op's value field, from the contract: `type` carries `text`, `press` carries `key`.
_ACTION_FIELDS: Final = {"fill": "value", "type": "text", "press": "key", "select": "values"}
# Snapshot xpaths can end at a text node, which a locator cannot target.
_TEXT_NODE: Final = re.compile(r"/text\(\)(?:\[\d+\])?$")


def browser_enabled() -> bool:
    """Whether this deployment offers the browser (``OPEN_SWE_BROWSER``).

    Off by default: the LangSmith browser is a beta each workspace must have enabled.
    """
    return os.environ.get("OPEN_SWE_BROWSER", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class _Session:
    """Stagehand attached to a thread's window."""

    browser: Any
    stagehand: Any
    ids: dict[str, str] = field(default_factory=dict)  # from the latest snapshot

    async def page(self) -> Any:
        pages = await self.browser.context.pages()
        return pages[-1] if pages else await self.browser.context.new_page()

    async def close(self) -> None:
        # Never `browser.close()`: on a browser we attached to it sends
        # `Browser.close`. Closing Stagehand frees the extension for the next client.
        with contextlib.suppress(Exception):  # best effort
            await asyncio.wait_for(self.stagehand.close(), 3)


def redact(text: str) -> str:
    """``text`` without the credentials a CDP URL carries."""
    return _SECRET_QUERY.sub(r"\1=[redacted]", text)


def thread_id(runtime: object) -> str:
    """The run's thread: tools carry it on ``runtime.config``, agent hooks ambiently.

    Open SWE's backend calls this deployment with its own service key, so there is
    no signed-in user to share a browser across threads (MDA's ``scope="user"``).
    """
    sources: list[object] = [getattr(runtime, "config", None)]
    with contextlib.suppress(RuntimeError):  # outside a graph run
        sources.append(get_config())
    for config in sources:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict) and configurable.get("thread_id"):
            return str(configurable["thread_id"])
    msg = "browser use needs a thread_id"
    raise RuntimeError(msg)


def browser_name(thread: str) -> str:
    """The thread's browser, keyed by deployment so two deployments never share one."""
    deployment = os.environ.get("LANGSMITH_DEPLOYMENT_NAME") or os.environ.get(
        "MDA_DEPLOYMENT_NAME", ""
    )
    digest = hashlib.sha256(f"{deployment}:{thread}".encode()).hexdigest()[:16]
    return f"open-swe-browser-{digest}"


def _credentials() -> dict[str, str]:
    """The deployment's LangSmith API key, and the workspace it is scoped to."""
    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        msg = "browser use needs the deployment's LangSmith API key"
        raise RuntimeError(msg)
    credentials = {"api_key": api_key}
    if workspace := os.environ.get("LANGSMITH_WORKSPACE_ID"):
        credentials["tenant_id"] = workspace
    return credentials


def _browsers_url() -> str:
    """The LangSmith browsers API (beta), at the endpoint the SDK's sandbox client uses."""
    endpoint = (
        os.environ.get("LANGSMITH_ENDPOINT")
        or os.environ.get("LANGCHAIN_ENDPOINT")
        or "https://api.smith.langchain.com"
    )
    return f"{endpoint.rstrip('/')}/v2/sandboxes/browsers"


def _check(resp: httpx2.Response, action: str) -> httpx2.Response:
    if resp.status_code == httpx2.codes.NOT_IMPLEMENTED:
        msg = f"could not {action}: the browser beta is not enabled for this workspace"
        raise RuntimeError(msg)
    if not resp.is_success:
        msg = f"could not {action}: HTTP {resp.status_code} {resp.text[:300]}"
        raise RuntimeError(msg)
    return resp


def _stagehand(extension_cdp_url: str) -> Any:  # stagehand is imported lazily
    """Stagehand, with its extension dialing Chrome from inside the browser sandbox.

    Stagehand hands the extension the URL the client connected with, which here
    is the API's and carries the deployment's key; the extension gets the
    window's local port instead.
    """
    from stagehand import Stagehand  # noqa: PLC0415

    class _Stagehand(Stagehand):
        def _worker_init_params(self, claimed: Any) -> Any:  # Stagehand 4.2.1 internals
            params = super()._worker_init_params(claimed)
            return params.model_copy(update={"browser_cdp_url": extension_cdp_url})

    return _Stagehand


async def _open(name: str, idle_ttl_seconds: int) -> _Session:
    """Attach Stagehand to the window of browser ``name``, creating either as needed."""
    from langsmith.sandbox import AsyncSandboxClient  # noqa: PLC0415
    from stagehand import local_browser  # noqa: PLC0415

    credentials = _credentials()
    headers = {"X-Api-Key": credentials["api_key"]}
    if "tenant_id" in credentials:
        headers["X-Tenant-Id"] = credentials["tenant_id"]
    base = f"{_browsers_url()}/{name}"
    api = httpx2.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT)
    sandboxes = AsyncSandboxClient(
        api_key=credentials["api_key"],
        **(
            {"headers": {"X-Tenant-Id": credentials["tenant_id"]}}
            if "tenant_id" in credentials
            else {}
        ),
    )
    async with api, sandboxes:
        made = await api.post(
            _browsers_url(), json={"name": name, "idle_ttl_seconds": idle_ttl_seconds}
        )
        if made.status_code != httpx2.codes.CONFLICT:  # conflict: it already exists
            _check(made, "create the browser")
        box = await sandboxes.get_sandbox(name)
        if box.status == "stopped":
            box = await sandboxes.start_sandbox(name, timeout=_SETUP_TIMEOUT_S) or box

        installed = await box.run(_INSTALL_EXTENSION, timeout=_SETUP_TIMEOUT_S)
        if installed.exit_code != 0:
            msg = f"could not install Stagehand in the browser: {installed.stderr[-500:]}"
            raise RuntimeError(msg)
        if "installed" in installed.stdout:  # Chrome loads extensions when it starts
            _check(await api.post(f"{base}/restart", json={"mode": "browser"}), "restart")

        windows = _check(await api.get(base), "look up the browser").json().get("attachments")
        window = next((w for w in windows or [] if w.get("name") == _WINDOW_NAME), None)
        if window is None:
            body = {"name": _WINDOW_NAME, "controller": "agent"}
            window = _check(await api.post(f"{base}/attachments", json=body), "open a window")
            window = window.json()
        port = await _window_port(box, window["id"])
        version = await api.get(f"{window['api_cdp_url']}/json/version")
        ws_url = _check(version, "reach the browser window").json()["webSocketDebuggerUrl"]

        cdp_url = f"{ws_url}{'&' if '?' in ws_url else '?'}{urlencode(credentials)}"
        local_url = f"ws://127.0.0.1:{port}/devtools/browser/{window['id']}"
        browser = await local_browser.connect(cdp_url=cdp_url)
        try:
            stagehand = await _stagehand(local_url).create(browser=browser)
        except Exception as err:
            # A browser that stopped mid-session resumes with the old client's
            # claim on the extension, which only a restart clears.
            if "already initialized" not in str(err):
                raise
            _check(await api.post(f"{base}/restart", json={"mode": "browser"}), "restart")
            browser = await local_browser.connect(cdp_url=cdp_url)
            stagehand = await _stagehand(local_url).create(browser=browser)
    return _Session(browser, stagehand)


async def _window_port(box: Any, window_id: str) -> int:
    """The port a window serves inside the browser sandbox; browserd saves it on open."""
    for _ in range(20):
        result = await box.run(f"cat {_WINDOWS_FILE}", timeout=60)
        with contextlib.suppress(ValueError):
            for window in json.loads(result.stdout).get("attachments") or []:
                if window.get("id") == window_id:
                    return int(window["port"])
        await asyncio.sleep(0.25)
    msg = "could not find the browser window's port"
    raise RuntimeError(msg)


#: Each browser's Stagehand client, and the lock its calls take turns on. Process-wide:
#: this middleware is built for every run, and a thread's runs share one client.
_SESSIONS: dict[str, _Session] = {}
_LOCKS: dict[str, asyncio.Lock] = {}

Opener = Callable[[str, int], Awaitable[_Session]]


class BrowserMiddleware(AgentMiddleware):
    """Give the agent a browser: ``run``, ``snapshot`` and ``screenshot``."""

    name = "open-swe-browser"

    def __init__(self, *, idle_ttl_seconds: int = 600, opener: Opener = _open) -> None:
        """Build the browser tools.

        Args:
            idle_ttl_seconds: Stop a browser after this long idle.
            opener: Attaches a session to a named browser; tests replace it.
        """
        super().__init__()
        self.idle_ttl_seconds = idle_ttl_seconds
        self._open = opener
        self._sessions = _SESSIONS
        self._locks = _LOCKS
        self.tools = self._build_tools()

    async def call(self, runtime: object, fn: Callable[[_Session, Any], Awaitable[Any]]) -> Any:
        """Run ``fn(session, page)``, reopening once if the session died.

        Errors come back as text: a browsing failure is something the model can
        route around. Results are not clipped here: deepagents' filesystem
        middleware moves a large one to a file the agent can read back.
        """
        try:
            key = thread_id(runtime)
        except RuntimeError as err:
            return f"error: {err}"
        # Stagehand serves one client per browser: calls on a browser take turns.
        async with self._locks.setdefault(key, asyncio.Lock()):
            for attempt in (1, 2):
                try:
                    if key not in self._sessions:
                        self._sessions[key] = await self._open(
                            browser_name(key), self.idle_ttl_seconds
                        )
                    session = self._sessions[key]
                    return await fn(session, await session.page())
                except Exception as err:  # noqa: BLE001 - recover or report
                    dead = any(marker in str(err).lower() for marker in _DEAD_SESSION)
                    if dead and attempt == 1:
                        await self._release(key)
                        continue
                    return redact(f"error: {type(err).__name__}: {err}")
        return "error: browser unavailable"

    async def _release(self, key: str) -> None:
        if (session := self._sessions.pop(key, None)) is not None:
            await session.close()

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        """Release the client between runs, so it never idles out holding the extension."""
        with contextlib.suppress(RuntimeError):  # no thread, so no session
            key = thread_id(runtime)
            async with self._locks.setdefault(key, asyncio.Lock()):
                await self._release(key)

    def _build_tools(self) -> list[BaseTool]:
        middleware = self

        @tool(description=SNAPSHOT_DESCRIPTION)
        async def snapshot(runtime: ToolRuntime, *, includeIframes: bool = True) -> str:  # noqa: N803 - contract name
            async def go(session: _Session, page: Any) -> str:
                result = await page.snapshot(include_iframes=includeIframes)
                session.ids = dict(result.xpath_map)
                return str(result.formatted_tree)

            return await middleware.call(runtime, go)

        @tool(description=RUN_DESCRIPTION)
        async def run(
            runtime: ToolRuntime,
            code: str | None = None,
            actions: list[dict[str, Any]] | None = None,
        ) -> str:
            if (code is None) == (actions is None):
                return "error: provide exactly one of `code` or `actions`."

            async def go(session: _Session, page: Any) -> str:
                if code is not None:
                    source, payload = _RUN_SOURCE % code, {}
                else:
                    steps = hydrate(session.ids, actions or [])
                    if isinstance(steps, str):
                        return steps
                    source, payload = _ACTIONS_SOURCE, {"steps": steps}
                result = await session.stagehand.experimental_batch(
                    source, payload, page=page, timeout=RUN_TIMEOUT_MS
                )
                return json.dumps(result, default=str, separators=(",", ":"))

            return await middleware.call(runtime, go)

        @tool(description=SCREENSHOT_DESCRIPTION)
        async def screenshot(
            runtime: ToolRuntime,
            *,
            fullPage: bool | None = None,  # noqa: N803 - contract name
            type: Literal["png", "jpeg"] = "png",
            quality: int | None = None,
        ) -> list[dict[str, Any]] | str:
            if quality is not None and not 0 <= quality <= MAX_QUALITY:
                return f"error: quality must be between 0 and {MAX_QUALITY}."
            if type == "png" and quality is not None:
                return "error: quality is only valid for jpeg screenshots."

            async def go(_session: _Session, page: Any) -> list[dict[str, Any]]:
                image = await page.screenshot(full_page=fullPage, type=type, quality=quality)
                data = base64.b64encode(image).decode("ascii")
                return [
                    {"type": "text", "text": "Screenshot of the current page:"},
                    {"type": "image", "url": f"data:image/{type};base64,{data}"},
                ]

            return await middleware.call(runtime, go)

        return [snapshot, run, screenshot]


def hydrate(ids: dict[str, str], actions: list[dict[str, Any]]) -> list[dict[str, Any]] | str:
    """Snapshot actions as selector steps, or why they cannot run."""
    if not actions:
        return "error: actions must not be empty."
    steps = []
    for action in actions:
        op = action.get("op")
        if op not in {"click", "hover", *_ACTION_FIELDS}:
            return f"error: unsupported op {op!r}. Use click, hover, fill, type, press or select."
        xpath = ids.get(str(action.get("id")))
        if xpath is None:
            ref = action.get("id")
            return f"error: snapshot ID {ref!r} is stale or not actionable; snapshot again."
        value = action.get(_ACTION_FIELDS[op]) if op in _ACTION_FIELDS else None
        if op in _ACTION_FIELDS and not isinstance(value, (str, list)):
            return f'error: {op} requires "{_ACTION_FIELDS[op]}".'
        steps.append({"op": op, "selector": f"xpath={_TEXT_NODE.sub('', xpath)}", "value": value})
    return steps
