"""The coding agent's LangSmith browser, reported to the backend so the dashboard can show it.

MDA's browser middleware names each thread's browser itself, from the
deployment. Each run tells the backend that name when it starts, so a person
can open the browser from the thread's Browser panel before the agent has, and
again once the agent first browses, so the panel opens on its own.
"""

import asyncio
import contextlib
import logging
from collections.abc import Mapping

from langchain.agents.middleware import AgentState
from langgraph.runtime import Runtime
from managed_deepagents import ManagedToolRuntime
from managed_deepagents.browser import BrowserMiddleware

from rt.backend import OpenSweBackend, run_body
from rt.tools import run_id

logger = logging.getLogger(__name__)


class ReportedBrowserMiddleware(BrowserMiddleware):
    """MDA's browser per thread: the backend hears its name once per process, its use once per run."""

    def __init__(self, backend: OpenSweBackend) -> None:
        super().__init__()
        self._backend = backend
        # (browser name, whether it is opened) already reported, or being reported.
        self._reported: set[tuple[str, bool]] = set()
        self._reporting: dict[tuple[str, bool], asyncio.Task[None]] = {}

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        with contextlib.suppress(RuntimeError):  # no thread, so no browser
            name = self.browser_name(runtime)
            # Sharing the thread deletes its browser, so each run reports the
            # agent's first browse again; a repeat changes nothing.
            self._reported.discard((name, True))
            self._report_later(name, state, opened=False)

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
            await self._backend.call(
                "browser", run_body(run_id(), state, browser=name, opened=opened)
            )
        except Exception:
            # Tried again on the next run or browser call; until then the panel cannot show it.
            logger.warning("Could not report the thread's browser", exc_info=True)
        else:
            self._reported.add(report)
        finally:
            self._reporting.pop(report, None)
