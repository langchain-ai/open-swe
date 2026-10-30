"""Safe, consistently namespaced runtime logging.

Each line goes to two sinks. Stdout, which the deployment's owner reads, gets
only the message. Inside a request or run log context, the line is also queued
on the process-wide sink for the LangSmith log collection endpoint, with the raw
error and attributes; that sink is ours, not the customer's.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from managed_deepagents._telemetry.otel.context import (
    LogAttributeValue,
    current_log_context,
    record_log,
)

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

    def info(
        self,
        message: str,
        *args: object,
        attributes: Mapping[str, LogAttributeValue] | None = None,
    ) -> None:
        """Write an informational runtime event."""
        line = _LazyLogMessage(message, args)
        self._logger.info("%s", line)
        if current_log_context() is not None:
            record_log("INFO", line.body(), attributes=attributes)

    def warning(
        self,
        message: str,
        *args: object,
        attributes: Mapping[str, LogAttributeValue] | None = None,
    ) -> None:
        """Write an expected but actionable runtime failure."""
        line = _LazyLogMessage(message, args)
        self._logger.warning("%s", line)
        if current_log_context() is not None:
            record_log("WARN", line.body(), attributes=attributes)

    def error(
        self,
        message: str,
        *args: object,
        error: BaseException | None = None,
        attributes: Mapping[str, LogAttributeValue] | None = None,
    ) -> None:
        """Write a runtime failure.

        ``error`` is sent as ``exception.type``, ``exception.message`` and
        ``exception.stacktrace`` to the log collection sink only; stdout gets
        the message alone.
        """
        line = _LazyLogMessage(message, args)
        self._logger.error("%s", line)
        if current_log_context() is not None:
            record_log("ERROR", line.body(), error=error, attributes=attributes)

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

    def body(self) -> str:
        """The rendered message without the stdout prefix."""
        return self._message % self._args if self._args else self._message

    def __str__(self) -> str:
        return f"{_LOG_PREFIX} {self.body()}"


logger = _Logger(logging.getLogger("mda"))


def _is_http_status(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and _HTTP_STATUS_MIN <= value <= _HTTP_STATUS_MAX
    )


__all__ = ["logger"]
