"""Non-2xx HTTP responses as errors a retry policy can classify."""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Final

_TOO_MANY_REQUESTS: Final = 429
_SERVER_ERROR: Final = 500


class HttpStatusError(Exception):
    """429 and 5xx are ``retryable``; other statuses will fail the same way again."""

    def __init__(self, status: int, retry_after_seconds: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.retryable = status == _TOO_MANY_REQUESTS or status >= _SERVER_ERROR

    @classmethod
    def from_response(cls, status: int, retry_after: str | None) -> HttpStatusError:
        return cls(status, _retry_after_seconds(retry_after))


def is_retryable(error: BaseException) -> bool:
    """HTTP 429/5xx, and any failure that is not an HTTP status, such as a timeout."""
    return error.retryable if isinstance(error, HttpStatusError) else True


def requested_delay(error: BaseException) -> float | None:
    return error.retry_after_seconds if isinstance(error, HttpStatusError) else None


def _retry_after_seconds(header: str | None) -> float | None:
    """``Retry-After`` as seconds or an HTTP date."""
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        pass
    else:
        return seconds if seconds >= 0 else None
    try:
        at = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    return max(at.timestamp() - time.time(), 0.0)
