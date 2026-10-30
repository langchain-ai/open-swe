"""Per-phase wall time of one managed graph build.

LangGraph Server logs only the total time of a slow graph factory. Every build
logs one line naming where that time went, in the message text: the deployment
log pipeline keeps the message but drops custom attributes. Mirrors TypeScript
``runtime/graph-build-timing.ts``, which cannot split ``connectors`` per MCP
server because its adapter discovers every server in one call.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, TypeVar

from managed_deepagents._log import logger

GraphBuildPhase = Literal[
    "factory",
    "context",
    "skills",
    "instructions",
    "connectors",
    "create_deep_agent",
]
GraphBuildKind = Literal["run", "read"]

_T = TypeVar("_T")


class GraphBuildTiming:
    """Wall time of each managed phase, from construction to :meth:`log`."""

    __slots__ = ("_clock", "_phases", "_sizes", "_started", "_steps")

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started = clock()
        self._phases: dict[GraphBuildPhase, float] = {}
        self._steps: dict[GraphBuildPhase, dict[str, float]] = {}
        self._sizes: dict[str, int] = {}

    @contextmanager
    def phase(self, name: GraphBuildPhase) -> Generator[None]:
        """Add the time spent in the block to ``name``; :func:`build_step` nests under it."""
        started = self._clock()
        token = _ACTIVE_PHASE.set((self, name))
        try:
            yield
        finally:
            _ACTIVE_PHASE.reset(token)
            self._phases[name] = self._phases.get(name, 0.0) + self._clock() - started

    def measure(self, name: GraphBuildPhase, run: Callable[[], _T]) -> _T:
        """Return ``run()`` and add its time to ``name``."""
        with self.phase(name):
            return run()

    @contextmanager
    def step(self, phase: GraphBuildPhase, name: str) -> Generator[None]:
        """Add the time spent in the block to step ``name`` of ``phase``."""
        started = self._clock()
        try:
            yield
        finally:
            steps = self._steps.setdefault(phase, {})
            steps[name] = steps.get(name, 0.0) + self._clock() - started

    def set_sizes(self, *, tools: int, middleware: int, subagents: int) -> None:
        """Record what the agent graph is built from."""
        self._sizes = {"tools": tools, "middleware": middleware, "subagents": subagents}

    def log(self, *, kind: GraphBuildKind, run_id: str | None) -> None:
        """Write the total, each phase in the order it first finished, and the sizes."""
        total_ms = _ms(self._clock() - self._started)
        scope = (
            f"kind={kind}"
            if run_id is None
            else f"kind={kind}, run_id={logger.log_identifier(run_id)}"
        )
        parts = [self._phase_text(name, seconds) for name, seconds in self._phases.items()]
        parts.extend(f"{name}={count}" for name, count in self._sizes.items())
        logger.info(
            "graph build took %sms (%s)%s",
            total_ms,
            scope,
            f": {' '.join(parts)}" if parts else "",
            attributes={
                "mda.graph_build.kind": kind,
                "mda.graph_build.total_ms": total_ms,
                **{
                    f"mda.graph_build.{name}_ms": _ms(seconds)
                    for name, seconds in self._phases.items()
                },
            },
        )

    def _phase_text(self, name: GraphBuildPhase, seconds: float) -> str:
        steps = " ".join(
            f"{logger.log_identifier(step)}={_ms(step_seconds)}ms"
            for step, step_seconds in self._steps.get(name, {}).items()
        )
        return f"{name}={_ms(seconds)}ms ({steps})" if steps else f"{name}={_ms(seconds)}ms"


_ACTIVE_PHASE: ContextVar[tuple[GraphBuildTiming, GraphBuildPhase] | None] = ContextVar(
    "mda_graph_build_phase", default=None
)


@contextmanager
def build_step(name: str) -> Generator[None]:
    """Time the block as step ``name`` of the running build phase; outside a build, do nothing."""
    active = _ACTIVE_PHASE.get()
    if active is None:
        yield
        return
    timing, phase = active
    with timing.step(phase, name):
        yield


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


__all__ = ["GraphBuildKind", "GraphBuildPhase", "GraphBuildTiming", "build_step"]
