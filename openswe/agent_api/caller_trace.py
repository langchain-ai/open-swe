"""Nest what the backend traces for an MDA call under the runtime span that made it."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from langsmith import tracing_context
from langsmith.run_trees import RunTree

logger = logging.getLogger(__name__)


@contextmanager
def traced_under(headers: Mapping[str, str]) -> Iterator[None]:
    """Trace the block under the span ``headers`` name, when they name one.

    Without it every hook would start a trace of its own, apart from the run's turn.
    """
    parent: RunTree | None = None
    if dotted_order := headers.get("langsmith-trace"):
        try:
            parent = RunTree.from_headers(
                {"langsmith-trace": dotted_order, "baggage": headers.get("baggage", "")}
            )
        except Exception:
            logger.warning("Ignoring unusable trace headers", exc_info=True)
    if parent is None:
        yield
        return
    with tracing_context(parent=parent):
        yield
