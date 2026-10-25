"""A shared point in time by which in-flight work must stop."""

from __future__ import annotations

import math
import time
from threading import Condition


class Deadline:
    """Unset until :meth:`set`; from then :meth:`remaining` counts down to zero.

    :meth:`wait` sleeps, but wakes as soon as the deadline passes, including
    one set while it sleeps.
    """

    def __init__(self) -> None:
        self._at: float | None = None
        self._changed = Condition()

    def set(self, seconds: float) -> None:
        """Pass ``seconds`` from now."""
        with self._changed:
            self._at = time.monotonic() + seconds
            self._changed.notify_all()

    def remaining(self) -> float:
        """Seconds left; infinite while unset."""
        at = self._at
        return math.inf if at is None else max(at - time.monotonic(), 0.0)

    def wait(self, seconds: float) -> None:
        """Sleep ``seconds``, or until the deadline passes."""
        end = time.monotonic() + seconds
        with self._changed:
            while (left := min(end - time.monotonic(), self.remaining())) > 0:
                self._changed.wait(left)
