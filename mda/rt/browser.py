"""The coding agent's LangSmith browser, reported to the backend so the dashboard can show it.

MDA's browser middleware names each thread's browser itself, from the
deployment. Each run tells the backend that name when it starts, so a person
can open the browser from the thread's Browser panel before the agent has, and
again once the agent first browses, so the panel opens on its own.
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime
from langsmith import tracing_context
from managed_deepagents import ManagedToolRuntime
from managed_deepagents.browser import (
    BrowserMiddleware,
    BrowserState,
    _reply,  # noqa: PLC2701 - notes a remade browser
    _Session,
)
from pydantic import JsonValue
from runtime_api.endpoint import call
from runtime_api.loop import BROWSER, BrowserReport

from rt.backend import OpenSweBackend
from rt.session_handoff import HeldSessions
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_INSTALLED = "Session installed."
_SESSION_HOSTS = re.compile(
    r"(?:(?:beta|eu|apac|aws|dev)\.)?smith\.langchain\.com"
    r"|pr-[0-9]+(?:-fe)?\.review\.smith\.langchain\.com"
)


class ReportedBrowserMiddleware(BrowserMiddleware):
    """MDA's browser per thread: the backend hears its name once per process, its use once per run."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend, *, enabled: bool = True) -> None:
        super().__init__()
        self._backend = backend
        # Every graph has the middleware, so all of them share one shape; only the
        # coding agent's has a browser.
        self._enabled = enabled
        # No browser_handoff: Open SWE runs only what its backend signs, so MDA's watcher
        # cannot start the run that tells the agent the user handed the browser back.
        self.tools = [tool for tool in self.tools if enabled and tool.name != "browser_handoff"]
        self._arguments.pop("browser_handoff", None)
        run = next((tool for tool in self.tools if tool.name == "browser_run"), None)
        if isinstance(run, StructuredTool) and run.coroutine is not None:
            original = run.coroutine

            async def run_with_session(
                runtime: ManagedToolRuntime, session_handle: str | None = None, **arguments: object
            ) -> object:
                return await self._run_with_session(original, runtime, session_handle, **arguments)

            run.coroutine = run_with_session
            if isinstance(run.args_schema, dict):
                run.args_schema["properties"]["session_handle"] = {
                    "type": "string",
                    "description": "An MCP result's session_handle: signs the browser in.",
                }
            self._arguments["browser_run"].add("session_handle")
        # (browser name, whether it is opened) already reported, or being reported.
        self._reported: set[tuple[str, bool]] = set()
        self._reporting: dict[tuple[str, bool], asyncio.Task[None]] = {}

    async def _run_with_session(
        self,
        original: Callable[..., Awaitable[object]],
        runtime: ManagedToolRuntime,
        session_handle: str | None = None,
        **arguments: object,
    ) -> object:
        if session_handle is None:
            return await original(runtime=runtime, **arguments)
        if arguments.get("code") or arguments.get("actions"):
            return "Install the session separately from browser code or actions."
        held = HeldSessions.of(self._backend)
        data = held.pop(session_handle, None) if held is not None else None
        if data is None:
            return "Unknown or used session_handle; mint a new session in this turn."
        host, key, session = data.get("host"), data.get("storage_key"), data.get("session")
        if not isinstance(host, str) or not _SESSION_HOSTS.fullmatch(host):
            return "The session's host is not a LangSmith site."
        if not isinstance(key, str) or not isinstance(session, dict):
            return "The MCP result held no browser session."
        with tracing_context(enabled=False):
            content, box = await self._call(runtime, partial(self._install, host, key, session))
        if content != _INSTALLED:
            logger.warning("Could not install a held browser session")
            content = "Could not install the browser session."
        return _reply(runtime, "browser_run", content, box)

    async def _install(
        self, host: str, key: str, value: dict[str, JsonValue], session: _Session
    ) -> str:
        page = await session.stagehand.browser.context.active_page()
        if page is None:
            raise ValueError("No active browser page")
        await page.goto(f"https://{host}")
        await page.evaluate(
            f"(() => {{ if (location.origin !== {json.dumps('https://' + host)}) "
            f"throw new Error('Wrong session origin'); "
            f"localStorage.setItem({json.dumps(key)}, {json.dumps(json.dumps(value))}); }})()"
        )
        await page.goto(f"https://{host}")
        return _INSTALLED

    async def abefore_agent(self, state: BrowserState, runtime: Runtime) -> None:
        if not self._enabled:
            return
        with contextlib.suppress(RuntimeError):  # no thread, so no browser
            name = self.browser_name(runtime)
            # Sharing the thread deletes its browser, so each run reports the
            # agent's first browse again; a repeat changes nothing.
            self._reported.discard((name, True))
            self._report_later(name, state, opened=False)

    async def aafter_agent(self, state: BrowserState, runtime: Runtime) -> None:
        if self._enabled:
            await super().aafter_agent(state, runtime)

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelResponse[object]:
        # The browser's instructions only where its tools are.
        if not self._enabled:
            return await handler(request)
        return await super().awrap_model_call(request, handler)

    async def _share(
        self, session: object, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        # Runs before every browser call, with the browser's name once it is open.
        await super()._share(session, name, key, runtime)  # ty: ignore[invalid-argument-type]
        self._report_later(name, runtime.state, opened=True)

    def _report_later(self, name: str, state: Mapping[str, object], *, opened: bool) -> None:
        report = (name, opened)
        if report in self._reported or report in self._reporting:
            return
        # In the background: a backend that is slow or down must not hold up the run.
        self._reporting[report] = asyncio.create_task(self._report(report, state))

    async def _report(self, report: tuple[str, bool], state: Mapping[str, object]) -> None:
        name, opened = report
        try:
            await call(
                self._backend,
                BROWSER,
                BrowserReport(run_id=run_id(), browser=name, opened=opened),
            )
        except Exception:
            # Tried again on the next run or browser call; until then the panel cannot show it.
            logger.warning("Could not report the thread's browser", exc_info=True)
        else:
            self._reported.add(report)
        finally:
            self._reporting.pop(report, None)
