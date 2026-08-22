"""Startup phase timings, replayed into the run trace as child spans.

Startup work happens in the graph factory and in the detached sandbox task,
both of which run outside the traced run, so they cannot open child spans where
the time is actually spent. Phases are timed where they happen, keyed by thread,
and replayed once a parent span exists.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables.config import ensure_config
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree

logger = logging.getLogger(__name__)

_MAX_THREADS = 512
_MAX_PHASES = 200


@dataclass(slots=True)
class _Phase:
    name: str
    start: datetime
    monotonic: float
    end: datetime | None = None
    elapsed_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


_PHASES: dict[str, list[_Phase]] = {}


def _open(thread_id: str, name: str, metadata: dict[str, Any]) -> _Phase | None:
    phases = _PHASES.get(thread_id)
    if phases is None:
        # A thread whose run dies before flushing leaves its phases behind.
        if len(_PHASES) >= _MAX_THREADS:
            _PHASES.pop(next(iter(_PHASES)), None)
        phases = _PHASES.setdefault(thread_id, [])
    if len(phases) >= _MAX_PHASES:
        return None
    phase = _Phase(
        name=name,
        start=datetime.now(UTC),
        monotonic=time.monotonic(),
        metadata={key: value for key, value in metadata.items() if value is not None},
    )
    phases.append(phase)
    return phase


def _close(phase: _Phase | None, error: BaseException | None) -> None:
    if phase is None:
        return
    phase.end = datetime.now(UTC)
    phase.elapsed_ms = int((time.monotonic() - phase.monotonic) * 1000)
    if error is not None:
        phase.error = f"{type(error).__name__}: {error}"


@asynccontextmanager
async def aphase(thread_id: str | None, name: str, **metadata: Any) -> AsyncIterator[None]:
    """Time a startup step for later replay into the trace."""
    if not thread_id:
        yield
        return
    phase = _open(thread_id, name, metadata)
    try:
        yield
    except BaseException as exc:
        _close(phase, exc)
        raise
    else:
        _close(phase, None)


def _parent_run_tree() -> RunTree | None:
    current = get_current_run_tree()
    if current is not None:
        return current
    # LangChain's tracer does not publish a run tree, so the current span has to
    # be recovered from the callback manager driving it.
    callbacks = ensure_config().get("callbacks")
    parent_run_id = getattr(callbacks, "parent_run_id", None)
    if parent_run_id is None:
        return None
    for handler in getattr(callbacks, "handlers", []):
        run_map = getattr(handler, "run_map", None)
        run = run_map.get(str(parent_run_id)) if isinstance(run_map, dict) else None
        if isinstance(run, RunTree) and run.dotted_order:
            return run
    return None


def _emit(parent: RunTree, phase: _Phase, outputs: dict[str, Any] | None = None) -> RunTree:
    # LangSmith clamps a child's start_time to its parent's, so a phase that ran
    # before the parent span opened draws a shortened bar. The real numbers ride
    # along on the span's inputs and outputs.
    child = parent.create_child(
        name=phase.name,
        run_type="chain",
        start_time=phase.start,
        inputs={
            "started_at": phase.start.isoformat(),
            **({"ended_at": phase.end.isoformat()} if phase.end else {}),
            **phase.metadata,
        },
    )
    child.post()
    child.end(
        outputs={
            **({"elapsed_ms": phase.elapsed_ms} if phase.elapsed_ms is not None else {}),
            **(outputs or {}),
        },
        error=phase.error,
        end_time=phase.end,
    )
    child.patch()
    return child


def flush_phases(thread_id: str | None) -> None:
    """Replay this thread's recorded phases as child spans of the current run."""
    phases = _PHASES.pop(thread_id, None) if thread_id else None
    if not phases:
        return
    try:
        parent = _parent_run_tree()
        if parent is None:
            logger.debug("No run tree to attach startup phases for thread %s", thread_id)
            return
        ordered = sorted(
            (entry for entry in phases if entry.end is not None), key=lambda item: item.start
        )
        if not ordered:
            return
        span = _Phase(
            name="startup",
            start=ordered[0].start,
            monotonic=0.0,
            end=max(entry.end for entry in ordered if entry.end is not None),
        )
        span.elapsed_ms = int((span.end - span.start).total_seconds() * 1000) if span.end else None
        wrapper = _emit(
            parent,
            span,
            outputs={
                "phases": [
                    {
                        "name": entry.name,
                        "elapsed_ms": entry.elapsed_ms,
                        "started_at": entry.start.isoformat(),
                    }
                    for entry in ordered
                ]
            },
        )
        for entry in ordered:
            _emit(wrapper, entry)
    except Exception:
        logger.debug("Could not replay startup phases for thread %s", thread_id, exc_info=True)
