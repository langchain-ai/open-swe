"""Phase timings for endpoints whose latency needs a breakdown."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import ClassVar, Self

from starlette.requests import HTTPConnection

logger = logging.getLogger(__name__)


@contextmanager
def phase(timings: dict[str, float], name: str) -> Iterator[None]:
    """Add how long the block took, in milliseconds, to ``name``'s time."""
    start = perf_counter()
    try:
        yield
    finally:
        timings[name] = timings.get(name, 0.0) + (perf_counter() - start) * 1000


def server_timing_header(timings: dict[str, float], counts: dict[str, int] | None = None) -> str:
    """``dur`` is milliseconds, so anything that is not a duration rides in ``desc``."""
    parts = [f"{name};dur={value:.1f}" for name, value in timings.items()]
    parts += [f"{name};desc={value}" for name, value in (counts or {}).items()]
    return ", ".join(parts)


class RequestTimings(dict[str, float]):
    """One request's phase timings in milliseconds, kept on the request so a dependency
    can record its phases before the handler adds its own and reports them all."""

    @classmethod
    def of(cls, request: HTTPConnection) -> Self:
        """The request's timings, empty until one of its parts records a phase."""
        timings = getattr(request.state, "phase_timings", None)
        if not isinstance(timings, cls):
            timings = cls()
            request.state.phase_timings = timings
        return timings

    _collecting: ClassVar[ContextVar[RequestTimings | None]] = ContextVar(
        "request_timings", default=None
    )

    @classmethod
    def record(cls, name: str, elapsed_ms: float) -> None:
        """Add a step's time to the timings being collected here, if any."""
        timings = cls._collecting.get()
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + elapsed_ms

    @classmethod
    @contextmanager
    def logged(cls, message: str, extra: dict[str, object]) -> Iterator[Self]:
        """Collect the steps the block records and log them, with its total, when it ends.

        ``extra`` is read at the end, so the block can add what it learns to it.

        Sampled traces keep too few slow requests to explain them; one log line each does.
        Steps nest (a parent's time includes its children's), so they don't sum to the total.
        """
        timings = cls()
        token = cls._collecting.set(timings)
        start = perf_counter()
        outcome = "error"
        try:
            yield timings
            outcome = "ok"
        finally:
            cls._collecting.reset(token)
            slowest = max(timings, key=timings.__getitem__, default=None)
            logger.info(
                message,
                extra={
                    **extra,
                    "outcome": outcome,
                    "elapsed_ms": round((perf_counter() - start) * 1000),
                    # Underscored so Datadog doesn't nest ``a.b`` into objects.
                    "steps_ms": {
                        name.replace(".", "_"): round(value) for name, value in timings.items()
                    },
                    "slowest_step": slowest,
                    "slowest_step_ms": round(timings[slowest]) if slowest is not None else None,
                },
            )
