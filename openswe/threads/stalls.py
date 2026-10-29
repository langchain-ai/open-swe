"""Fail runs that stopped responding, so the person hears it instead of silence.

A frozen run still reports itself running: no run-complete webhook comes, and
the status reconcile keeps finding its thread busy. A run that has recorded
nothing in its transcript for ``STALLED_AFTER`` is reported failed through the
path a failed run's webhook takes, then cancelled on MDA. It is never started
again: runs re-sent after freezing froze again.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Self

from pydantic import JsonValue

from openswe import event_claims
from openswe.completion import handle_run_completion
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.threads.runtimes import RUNTIMES, MdaRun
from openswe.threads.status import refresh_thread_status
from openswe.transcript.turns import last_run_activity

logger = logging.getLogger(__name__)

# Twice the longest a healthy run goes quiet: a model call's three 900s attempts.
STALLED_AFTER: Final = timedelta(minutes=90)
_CLAIM_SCOPE: Final = "stalled_run"
_CLAIM_TTL: Final = timedelta(days=2)
_ERROR: Final[dict[str, JsonValue]] = {
    "error": "RunStalledError",
    "message": "The run stopped responding",
}


def _claim_key(thread_id: str, run_id: str) -> str:
    return f"{thread_id}:{run_id}"


@dataclass(frozen=True)
class StalledRun:
    """A thread's active run that has gone quiet past ``STALLED_AFTER``."""

    thread: IndexedThread
    run: MdaRun
    quiet_since: datetime

    @classmethod
    async def find(cls, thread: IndexedThread, now: datetime) -> Self | None:
        """The run holding the thread, if quiet too long.

        A run already failed that ignored its cancel is passed over, so a resend
        queued behind it is timed on its own instead of waiting unseen.
        """
        for run in await RUNTIMES.mda_runtime.active_runs(thread.thread_id):
            if not await event_claims.held(_CLAIM_SCOPE, _claim_key(thread.thread_id, run.run_id)):
                return await cls._if_quiet(thread, run, now)
        return None

    @classmethod
    async def _if_quiet(cls, thread: IndexedThread, run: MdaRun, now: datetime) -> Self | None:
        activity = await last_run_activity(thread.thread_id, run.run_id)
        quiet_since = max(run.created_at, activity) if activity else run.created_at
        return cls(thread, run, quiet_since) if now - quiet_since >= STALLED_AFTER else None

    @property
    def thread_id(self) -> str:
        return self.thread.thread_id

    @property
    def run_id(self) -> str:
        return self.run.run_id

    async def fail(self, now: datetime) -> bool:
        """Report the run failed and cancel it, once.

        ``False`` when another sweep holds it, or it ended on its own meanwhile, in
        which case its webhook reports the real outcome.
        """
        key = _claim_key(self.thread_id, self.run_id)
        if not await event_claims.claim(_CLAIM_SCOPE, key, ttl=_CLAIM_TTL):
            return False
        mda = RUNTIMES.mda_runtime
        current = await mda.run(self.thread_id, self.run_id)
        if current is not None and not current.active:
            logger.info(
                "A quiet run ended before it was failed as stalled",
                extra={
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "run_status": current.status,
                },
            )
            return False
        source = self.thread.metadata.get("source")
        logger.warning(
            "Failing a run that stopped responding",
            extra={
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "run_age_seconds": int((now - self.run.created_at).total_seconds()),
                "quiet_seconds": int((now - self.quiet_since).total_seconds()),
                "origin": source if isinstance(source, str) else None,
            },
        )
        try:
            await handle_run_completion(
                {
                    "thread_id": self.thread_id,
                    "run_id": self.run_id,
                    "status": "error",
                    "error": _ERROR,
                    "metadata": self.run.metadata,
                }
            )
        except Exception:
            await event_claims.release(_CLAIM_SCOPE, key)
            raise
        # Reported before cancelling, so a cancel the run acts on finds its turn closed.
        await mda.cancel(self.thread_id, self.run_id)
        if await refresh_thread_status(self.thread_id) == "busy":
            # A run frozen past cancelling keeps reporting busy; the index stops showing it.
            for run in await mda.active_runs(self.thread_id):
                if not await event_claims.held(
                    _CLAIM_SCOPE, _claim_key(self.thread_id, run.run_id)
                ):
                    break
            else:
                await index.set_status(self.thread_id, "error")
        return True


async def fail_stalled_runs() -> int:
    """Fail the runs of threads busy past ``STALLED_AFTER`` that went quiet that long."""
    now = datetime.now(UTC)
    failed = 0
    for thread in await index.busy_since(now - STALLED_AFTER):
        if thread.runtime != "mda":
            continue
        try:
            stalled = await StalledRun.find(thread, now)
            failed += stalled is not None and await stalled.fail(now)
        except Exception:
            logger.exception(
                "Could not check a busy thread for a stalled run",
                extra={"thread_id": thread.thread_id},
            )
    return failed
