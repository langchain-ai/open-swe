"""Keep optional analytics capture from interrupting product operations."""

import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from agent.database.analytics import configured

logger = logging.getLogger(__name__)


def fail_soft[**P](operation: Callable[P, Awaitable[None]]) -> Callable[P, Awaitable[None]]:
    @wraps(operation)
    async def capture(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            if configured():
                await operation(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Analytics capture failed",
                extra={
                    "analytics_operation": getattr(operation, "__name__", type(operation).__name__)
                },
                exc_info=True,
            )

    return capture
