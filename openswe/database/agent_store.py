"""Base for PostgreSQL-backed stores the agent's ``StoreBackend`` routes read."""

import asyncio
from collections.abc import Iterable

from langgraph.store.base import BaseStore, Op, Result


class AgentStore(BaseStore):
    """Runs sync calls, which deepagents makes from worker threads, on the loop that built it."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return asyncio.run_coroutine_threadsafe(self.abatch(list(ops)), self._loop).result()
