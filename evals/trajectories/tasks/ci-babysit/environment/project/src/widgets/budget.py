"""A per-client retry budget."""

import time


class RetryBudget:
    """Allows ``limit`` retries per client in each ``window`` seconds."""

    def __init__(self, limit: int = 10, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._spent: dict[str, list[float]] = {}

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        recent = [t for t in self._spent.get(client, []) if now - t < self.window]
        if len(recent) >= self.limit:
            self._spent[client] = recent
            return False
        self._spent[client] = [*recent, now]
        return True
