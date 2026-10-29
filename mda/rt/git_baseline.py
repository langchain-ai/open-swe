"""The turn's git baseline, taken while the run's first model call runs.

The backend snapshots the thread's checkouts (``git_baseline``) for the turn's git
trace to measure what its tools change. A model call changes nothing in the box, so
the snapshot starts once preparation is done and is waited for only once the model
has answered, before any tool it calls runs.
"""

import asyncio
import logging

from pydantic import JsonValue
from runtime_api.endpoint import call
from runtime_api.loop import GIT_BASELINE, GitBaseline
from runtime_api.operations import RunRequest

from rt.backend import BackendCallError, OpenSweBackend
from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)


class TurnBaseline:
    """One run's baseline, from when it is asked for until the run takes it."""

    def __init__(self) -> None:
        self._taking: asyncio.Task[GitBaseline] | None = None

    def start(self, backend: OpenSweBackend, run_id: str | None) -> None:
        self._taking = asyncio.create_task(call(backend, GIT_BASELINE, RunRequest(run_id=run_id)))

    async def taken(self) -> dict[str, JsonValue]:
        """The run state the baseline goes in, the first time it is asked for; empty after
        that, or when the backend could not take it, as a snapshot it could not take is."""
        taking, self._taking = self._taking, None
        if taking is None:
            return {}
        try:
            baseline = await taking
        except BackendCallError:
            logger.warning("Could not take the turn's git baseline", exc_info=True)
            return {}
        TurnTrace.record_evidence(baseline.update)
        return baseline.update
