"""What a thread's monitors may do between the messages people send it.

Each human message opens a fresh allowance, kept in the thread's metadata: a
number of monitor starts, a number of events, and a window of wall-clock time
that every monitor's timeout is clipped to. Monitors stop when any runs out, so
a thread nobody is talking to cannot keep spending.
"""

import time
from collections.abc import Callable, Mapping
from typing import Final, Self

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ValidationError

from openswe.monitors.sandbox import MonitorError
from openswe.threads.oswe_thread import OsweThreadState
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

MAX_STARTS: Final = 10
MAX_EVENTS: Final = 50
WINDOW_SECONDS: Final = 24 * 3600
METADATA_KEY: Final = "monitor_budget"
EVENTS_SPENT_REASON: Final = f"{MAX_EVENTS} monitor events since the last human message"


class MonitorBudgetSpent(MonitorError):
    """A monitor start the thread's allowance does not cover."""


class MonitorBudget(BaseModel):
    """One human message's allowance and what the thread's monitors used of it."""

    generation: str = ""
    opened_at: float = 0
    starts: int = 0
    events: int = 0

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self:
        try:
            return cls.model_validate(metadata.get(METADATA_KEY) or {})
        except ValidationError:
            return cls()

    @property
    def window_ends_at(self) -> float:
        return self.opened_at + WINDOW_SECONDS


class ThreadMonitorBudget:
    """Spends ``thread_id``'s monitor allowance, opening a new one after each human message."""

    def __init__(self, client: LangGraphClient, thread_id: str) -> None:
        self._client = client
        self._thread_id = thread_id

    async def start(self) -> int:
        """Count one monitor start and return the seconds it may run."""

        def spend(budget: MonitorBudget, now: float) -> int:
            remaining = int(budget.window_ends_at - now)
            if budget.starts >= MAX_STARTS:
                raise MonitorBudgetSpent(
                    f"{MAX_STARTS} monitors were started since the last human message; "
                    "wait for one before starting another"
                )
            if budget.events >= MAX_EVENTS:
                raise MonitorBudgetSpent(
                    f"monitors emitted {MAX_EVENTS} events since the last human message; "
                    "wait for one before starting another"
                )
            if remaining <= 0:
                raise MonitorBudgetSpent(
                    "monitors may run for 24 hours after a human message; "
                    "wait for one before starting another"
                )
            budget.starts += 1
            return remaining

        return await self._update(spend)

    async def take_events(self, wanted: int) -> int:
        """Count up to ``wanted`` events and return how many the allowance covers."""

        def spend(budget: MonitorBudget, _now: float) -> int:
            granted = max(0, min(wanted, MAX_EVENTS - budget.events))
            budget.events += granted
            return granted

        return await self._update(spend) if wanted else 0

    async def _update[T](self, spend: Callable[[MonitorBudget, float], T]) -> T:
        generation = (
            await OsweThreadState.get(self._client, self._thread_id)
        ).latest_human_message_key
        async with agent_thread_pr_state_lock(self._thread_id):
            now = time.time()
            budget = MonitorBudget.of(
                thread_metadata(await self._client.threads.get(self._thread_id))
            )
            if budget.generation != generation:
                budget = MonitorBudget(generation=generation, opened_at=now)
            result = spend(budget, now)
            await self._client.threads.update(
                self._thread_id, metadata={METADATA_KEY: budget.model_dump()}
            )
            return result
