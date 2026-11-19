"""Phase timings for endpoints whose latency needs a breakdown."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Self

from starlette.requests import HTTPConnection


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
