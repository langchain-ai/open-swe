"""Phase timings for endpoints whose latency needs a breakdown."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter


@contextmanager
def phase(timings: dict[str, float], name: str) -> Iterator[None]:
    """Record how long the block took, in milliseconds, under ``name``."""
    start = perf_counter()
    try:
        yield
    finally:
        timings[name] = (perf_counter() - start) * 1000


def server_timing_header(timings: dict[str, float]) -> str:
    return ", ".join(f"{name};dur={value:.1f}" for name, value in timings.items())
