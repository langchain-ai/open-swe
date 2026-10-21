"""The coding agent's LangSmith browser, reported to the backend so the dashboard can show it.

MDA's browser middleware names and opens each thread's browser itself. The
first time a run browses, this tells the backend which browser it is, so the
thread's Browser panel can frame the window the agent drives.
"""

import logging

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

    async def _share(
        self, session: object, name: str, key: str, runtime: ManagedToolRuntime
    ) -> None:
        # Runs before every browser call, with the browser's name once it is open.
        await super()._share(session, name, key, runtime)  # ty: ignore[invalid-argument-type]
        if name in self._reported:
            return
        try:
            await self._backend.call("browser", run_body(run_id(), runtime.state, browser=name))
        except Exception:
            # Browsing goes on; the panel only shows the browser once the backend hears of it.
            logger.warning("Could not report the thread's browser", exc_info=True)
            return
        self._reported.add(name)
