"""Retry a failing call with jittered exponential backoff."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from managed_deepagents._concurrency import Deadline

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    #: Whether an error is worth another attempt.
    retry_on: Callable[[BaseException], bool]
    #: A delay the error asks for, such as an HTTP ``Retry-After``. Capped at
    #: ``max_delay_seconds``.
    delay_for: Callable[[BaseException], float | None] | None = None


def with_retry(fn: Callable[[T, Deadline], R], policy: RetryPolicy) -> Callable[[T, Deadline], R]:
    """``fn``, retried under ``policy``. The last error is re-raised.

    A backoff ends early once ``deadline`` passes, and no attempt follows it.
    """

    def retried(arg: T, deadline: Deadline) -> R:
        attempt = 1
        while True:
            try:
                return fn(arg, deadline)
            except Exception as error:
                if (
                    attempt >= policy.max_attempts
                    or not policy.retry_on(error)
                    or deadline.remaining() <= 0
                ):
                    raise
                asked = policy.delay_for(error) if policy.delay_for else None
                delay = asked if asked is not None else backoff_seconds(attempt, policy)
                deadline.wait(min(delay, policy.max_delay_seconds))
                if deadline.remaining() <= 0:
                    raise
                attempt += 1

    return retried


def backoff_seconds(attempt: int, policy: RetryPolicy) -> float:
    """Jitter over the upper half of the exponential step, capped."""
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (attempt - 1))
    return ceiling / 2 + random.random() * (ceiling / 2)  # noqa: S311 - jitter, not security
