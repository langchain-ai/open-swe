"""The coding agent's LangSmith browser, reported to the backend so the dashboard can show it.

MDA's browser middleware names and opens each thread's browser itself. The
first time a run browses, this tells the backend which browser it is, so the
thread's Browser panel can frame the window the agent drives.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from managed_deepagents import ManagedToolRuntime
from managed_deepagents.browser import BrowserMiddleware

from rt.backend import OpenSweBackend, run_body
from rt.tools import run_id

logger = logging.getLogger(__name__)


class ReportedBrowserMiddleware(BrowserMiddleware):
    """MDA's browser per thread, whose name the backend hears once per run."""

    def __init__(self, backend: OpenSweBackend) -> None:
        super().__init__()
        self._backend = backend
        self._reported: set[str] = set()
        self._reporting: dict[str, asyncio.Task[None]] = {}

    async def _share(
        self, session: object, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        # Runs before every browser call, with the browser's name once it is open.
        await super()._share(session, name, key, runtime)  # ty: ignore[invalid-argument-type]
        if name in self._reported or name in self._reporting:
            return
        # In the background: a backend that is slow or down must not hold up browsing.
        self._reporting[name] = asyncio.create_task(self._report(name, runtime.state))

    async def _wake(self, runtime: ManagedToolRuntime, message: dict[str, Any]) -> None:
        # Open SWE runs only what its backend signs, so the backend starts the run that
        # notifies the thread, as it delivers a worker's result. ``_wake`` is MDA's
        # private method, like ``_share``: it may change with MDA.
        # The watcher calls this once the run has ended, so there is no run id.
        body = run_body(None, {}, text=message["content"], notification=message["mda_notification"])
        await self._backend.call("wake", body)

    async def _report(self, name: str, state: Mapping[str, object]) -> None:
        try:
            await self._backend.call("browser", run_body(run_id(), state, browser=name))
        except Exception:
            # Tried again on the next browser call; until then the panel cannot show it.
            logger.warning("Could not report the thread's browser", exc_info=True)
        else:
            self._reported.add(name)
        finally:
            self._reporting.pop(name, None)
