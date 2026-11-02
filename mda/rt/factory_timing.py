"""How long the factory took to define a graph, phase by phase.

langgraph-api times each graph access as a whole ("Slow graph load"); this line,
logged as the factory returns, splits the factory's share of it. What is left of
that time is MDA compiling the definition (``compile_managed_agent_async``).
"""

import logging
from time import perf_counter

logger = logging.getLogger(__name__)


class FactoryTiming:
    """Milliseconds per phase of one factory call, logged once at the end."""

    def __init__(self) -> None:
        self._started = self._last = perf_counter()
        self._phases: dict[str, float] = {}

    def lap(self, phase: str) -> None:
        """Close ``phase``: the time since the previous lap, or since the start."""
        now = perf_counter()
        self._phases[f"{phase}_ms"] = _ms(now - self._last)
        self._last = now

    def log(self, **attributes: str | int | bool | None) -> None:
        logger.info(
            "Defined the graph",
            extra={
                **self._phases,
                "factory_ms": _ms(perf_counter() - self._started),
                **attributes,
            },
        )


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)
