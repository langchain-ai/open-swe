"""How many of the installation's GitHub calls each run made, as LangSmith feedback.

``openswe.github.metering`` logs every backend request with the run that sent it; this
tallies the ones that spend Robin's App installation quota per run and posts the total
on the run's trace as ``github_calls`` feedback, so LangSmith dashboards can chart
GitHub usage by run kind, origin or person beside cost and latency.

Each process counts the requests it sent and writes its own feedback row on the run,
so a run served by several replicas, or by a replica that restarted, sums correctly.
Calls no run made (background jobs) stay in the request logs alone, and so do a
sandbox's own ``gh`` calls, which never pass through the backend.

The tally is telemetry, kept in the process like the request logs and Datadog spans it
sits beside: a process that dies loses at most its last minute of counts, and nothing
reads them back. Durable intent and the job queue are for work that must happen.
"""

import asyncio
import contextlib
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Final

from openswe.config import ENV

logger = logging.getLogger(__name__)

FEEDBACK_KEY: Final = "github_calls"
FLUSH_SECONDS: Final = 60.0
# A run quiet this long is done: its total is posted by now, or never will be.
_FORGET_SECONDS: Final = 2 * 60 * 60.0


@dataclass
class RunGitHubCalls:
    """This process's per-run count of quota-spending GitHub calls, posted each minute."""

    # Unique to the process: a restart on the same host adds a row instead of replacing one.
    process: str = field(default_factory=lambda: f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}")
    _counts: dict[str, int] = field(default_factory=dict)
    _seen: dict[str, float] = field(default_factory=dict)
    _unposted: set[str] = field(default_factory=set)
    _task: asyncio.Task[None] | None = None

    def record(self, run_id: str) -> None:
        """Count one call ``run_id`` made against the installation's quota."""
        self._counts[run_id] = self._counts.get(run_id, 0) + 1
        self._seen[run_id] = time.monotonic()
        self._unposted.add(run_id)

    async def flush(self) -> None:
        """Post the total of every run that made calls since the last flush; a total
        LangSmith refused is posted again next time, until the run is forgotten."""
        # The LangSmith helpers import the agent's dependencies; metering loads with the client.
        from openswe.utils.langsmith import create_langsmith_feedback

        if not ENV.LANGSMITH_API_KEY.optional():
            # Nowhere to post: keep nothing.
            self._counts.clear()
            self._seen.clear()
            self._unposted.clear()
            return
        pending, self._unposted = self._unposted, set()
        try:
            while pending:
                run_id = next(iter(pending))
                count = self._counts.get(run_id)
                posted = count is None
                if count is not None:
                    try:
                        posted = await create_langsmith_feedback(
                            run_id,
                            FEEDBACK_KEY,
                            score=count,
                            identity=f"{FEEDBACK_KEY}:{self.process}",
                        )
                    except Exception:
                        logger.warning("Could not post a run's GitHub calls", exc_info=True)
                pending.discard(run_id)
                if not posted:
                    self._unposted.add(run_id)
        finally:
            # Cancelled mid-flush, as at shutdown: what was not posted is still owed.
            self._unposted |= pending
        cutoff = time.monotonic() - _FORGET_SECONDS
        for run_id in [r for r, seen in self._seen.items() if seen < cutoff]:
            self._counts.pop(run_id, None)
            self._seen.pop(run_id, None)
            self._unposted.discard(run_id)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="github-run-calls")

    async def stop(self) -> None:
        """Stop the loop and post what is left."""
        if (task := self._task) is not None:
            self._task = None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.flush()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 - the loop outlives any one flush
                logger.warning("Posting runs' GitHub calls failed", exc_info=True)


RUN_GITHUB_CALLS: Final = RunGitHubCalls()
