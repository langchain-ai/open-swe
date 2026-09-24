"""Wait for background work a test cannot await directly."""

import asyncio
from collections.abc import Callable

_ATTEMPTS = 100
_INTERVAL_SECONDS = 0.01


async def eventually(condition: Callable[[], bool]) -> None:
    """Yield to the event loop until ``condition`` holds, failing after about a second."""
    for _ in range(_ATTEMPTS):
        if condition():
            return
        await asyncio.sleep(_INTERVAL_SECONDS)
    if condition():
        return
    raise AssertionError("condition did not hold before the background work settled")
