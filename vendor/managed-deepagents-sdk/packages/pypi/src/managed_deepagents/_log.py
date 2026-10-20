"""Safe, consistently namespaced runtime logging."""

from __future__ import annotations

import logging
from typing import Final

_LOG_PREFIX: Final = "[mda]"
_LOG_IDENTIFIER_MAX_CHARS: Final = 128
_LOG_IDENTIFIER_PREFIX_CHARS: Final = 125
_HTTP_STATUS_MIN: Final = 100
_HTTP_STATUS_MAX: Final = 599


class _Logger:
    """Level-specific logger that accepts messages and lazy format arguments only."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, message: str, *args: object) -> None:
        """Write an informational runtime event."""
        self._logger.info("%s", _LazyLogMessage(message, args))

    def warning(self, message: str, *args: object) -> None:
        """Write an expected but actionable runtime failure."""
        self._logger.warning("%s", _LazyLogMessage(message, args))

    def error(self, message: str, *args: object) -> None:
        """Write a runtime failure without accepting raw exception metadata."""
        self._logger.error("%s", _LazyLogMessage(message, args))

    def safe_error_label(self, error: object) -> str:
        """Return an exception type and bounded HTTP status, never its message."""
        name = type(error).__name__
        status = getattr(error, "status", None)
        if not _is_http_status(status):
            status = getattr(error, "status_code", None)
        if not _is_http_status(status):
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None)
        return f"{name} HTTP {status}" if _is_http_status(status) else name

    def log_identifier(self, value: str) -> str:
        """Sanitize and bound an untrusted identifier for one physical log line."""
        printable = "".join(
            character if self.is_log_printable(character) else "?" for character in value
        )
        if len(printable) <= _LOG_IDENTIFIER_MAX_CHARS:
            return printable
        return f"{printable[:_LOG_IDENTIFIER_PREFIX_CHARS]}..."

    def is_log_printable(self, character: str) -> bool:
        """True when a character can appear in a log line without forging a new one."""
        return character.isprintable()


class _LazyLogMessage:
    """Delay prefixing and interpolation until logging renders an enabled level."""

    __slots__ = ("_args", "_message")

    def __init__(self, message: str, args: tuple[object, ...]) -> None:
        self._message = message
        self._args = args

    def __str__(self) -> str:
        rendered = self._message % self._args if self._args else self._message
        return f"{_LOG_PREFIX} {rendered}"


logger = _Logger(logging.getLogger("mda"))


def _is_http_status(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and _HTTP_STATUS_MIN <= value <= _HTTP_STATUS_MAX
    )


__all__ = ["logger"]
