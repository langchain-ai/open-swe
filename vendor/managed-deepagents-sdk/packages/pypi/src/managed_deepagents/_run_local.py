"""Values that belong to the run in progress, not to the graph that holds them.

A cached graph (see ``CachedAgent``) is compiled once and serves many runs,
so any object it holds is shared by all of them. State that belongs to one run,
such as a Context Hub snapshot or which proxy config a run already applied, goes
in a ``RunLocal`` instead: the graph holds the ``RunLocal``, and each run gets
its own value from it.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Generic, TypeVar, cast

from langgraph.config import get_config

T = TypeVar("T")

# How many runs' values one RunLocal keeps. Past this, the oldest run's value is
# dropped; if that run is still going, its next get() makes a fresh value.
RUNS_KEPT = 256

_UNSET = object()


def current_run_id() -> str | None:
    """Return the Agent Server run_id of the run in progress.

    Returns None when there is no run_id: when called outside a LangGraph run (for
    example in unit tests), or when the caller did not set ``metadata.run_id``.
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    metadata = config.get("metadata")
    value = metadata.get("run_id") if isinstance(metadata, Mapping) else None
    return str(value) if value else None


class RunLocal(Generic[T]):
    """One value per run, created on the run's first ``get()``.

    Calls made outside a run (no run_id) all share one value, which is how the
    state behaved before graphs were cached.

    ``first``, when given, is used as the first run's value instead of calling
    ``make``. An uncached graph serves exactly one run, so passing the object built
    at compile time as ``first`` means that run reuses it, as it did before.
    """

    def __init__(self, make: Callable[[], T], *, first: object = _UNSET) -> None:
        self._make = make
        self._first = first
        self._values: OrderedDict[str | None, T] = OrderedDict()
        self._lock = threading.Lock()

    def get(self) -> T:
        run_id = current_run_id()
        with self._lock:
            if run_id in self._values:
                self._values.move_to_end(run_id)
                return self._values[run_id]
            if self._first is not _UNSET:
                value = cast("T", self._first)
                self._first = _UNSET
            else:
                value = self._make()
            self._values[run_id] = value
            while len(self._values) > RUNS_KEPT:
                self._values.popitem(last=False)
            return value

    def clear(self) -> None:
        """Forget the current run's value, so its next ``get()`` makes a new one."""
        run_id = current_run_id()
        with self._lock:
            self._values.pop(run_id, None)
