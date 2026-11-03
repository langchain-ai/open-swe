"""Per-request log context for the OTel log forwarder.

Ingress and the run execute in different processes, so each holds its own
context, and records go straight to the process-wide log sink stamped with that
context's ids. Both share one trace id: ingress takes it
from the incoming ``traceparent`` (or mints one) and hands it to the run as
``config["metadata"]["mda_trace_id"]``. Each side gets its own span id so
Datadog can tell which process wrote a record.
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Final, Literal

from managed_deepagents._telemetry.otel.exporter import log_sink
from managed_deepagents._telemetry.otel.truncate import truncate_middle

#: Run metadata key that carries the trace id from ingress into the run.
TRACE_ID_METADATA_KEY: Final = "mda_trace_id"

LogSeverity = Literal["INFO", "WARN", "ERROR"]
LogAttributeValue = str | int | float | bool
LogAttributes = dict[str, LogAttributeValue]

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def new_trace_id() -> str:
    """A fresh 16-byte trace id as 32 lowercase hex characters."""
    return secrets.token_hex(16)


def new_span_id() -> str:
    """A fresh 8-byte span id as 16 lowercase hex characters."""
    return secrets.token_hex(8)


@dataclass
class LogRecord:
    #: Unique to this record and kept across retries, so copies of a batch sent
    #: twice can be told apart from distinct records.
    uid: str
    time_unix_nano: int
    severity: LogSeverity
    body: str
    attributes: LogAttributes
    trace_id: str
    span_id: str


@dataclass
class LogContext:
    trace_id: str
    span_id: str = field(default_factory=new_span_id)
    #: Attached to every later record, so a fact learned mid-request sticks.
    attributes: LogAttributes = field(default_factory=dict)
    #: Work that outlives the request; ingress completes after it settles.
    pending: list[Awaitable[object]] = field(default_factory=list)


_current: ContextVar[LogContext | None] = ContextVar("mda_log_context", default=None)


def _is_valid_trace_id(value: str) -> bool:
    return bool(_TRACE_ID.match(value)) and value.strip("0") != ""


def trace_id_from_traceparent(header: str | None) -> str:
    """The trace id of a W3C ``traceparent`` header, or a new one."""
    match = _TRACEPARENT.match((header or "").strip().lower())
    trace_id = match.group(1) if match else None
    return trace_id if trace_id and _is_valid_trace_id(trace_id) else new_trace_id()


def trace_id_from_metadata(metadata: Mapping[str, object] | None) -> str:
    """A trace id handed over through run metadata, or a new one."""
    value = (metadata or {}).get(TRACE_ID_METADATA_KEY)
    return value if isinstance(value, str) and _is_valid_trace_id(value) else new_trace_id()


def current_log_context() -> LogContext | None:
    return _current.get()


def enter_log_context(context: LogContext) -> Token[LogContext | None]:
    """Make ``context`` current; pass the token to :func:`exit_log_context`."""
    return _current.set(context)


def exit_log_context(token: Token[LogContext | None]) -> None:
    _current.reset(token)


def hold_log_context(work: Awaitable[object]) -> None:
    """Keep the current context open until ``work`` settles."""
    context = current_log_context()
    if context is not None:
        context.pending.append(work)


def set_log_attributes(attributes: Mapping[str, LogAttributeValue | None]) -> None:
    """Add attributes to the current context's later records."""
    context = current_log_context()
    if context is None:
        return
    for key, value in attributes.items():
        if value is not None:
            context.attributes[key] = value


def exception_attributes(error: BaseException) -> LogAttributes:
    """OTel ``exception.*`` attributes for an error."""
    import traceback  # noqa: PLC0415 - only on the error path

    return {
        "exception.type": type(error).__name__,
        "exception.message": str(error),
        "exception.stacktrace": "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    }


def record_log(
    severity: LogSeverity,
    body: str,
    *,
    error: BaseException | None = None,
    attributes: Mapping[str, LogAttributeValue] | None = None,
) -> None:
    """Queue a record under the current context's ids on the process-wide sink.

    Any string over the per-value limit has its middle cut. Does nothing
    outside a context or when the sink is disabled.
    """
    context = current_log_context()
    sink = log_sink()
    if context is None or not sink.enabled:
        return
    merged: LogAttributes = {
        **context.attributes,
        **(attributes or {}),
        **(exception_attributes(error) if error is not None else {}),
    }
    sink.push(
        LogRecord(
            uid=str(uuid.uuid4()),
            time_unix_nano=time.time_ns(),
            severity=severity,
            body=truncate_middle(body),
            attributes={
                key: truncate_middle(value) if isinstance(value, str) else value
                for key, value in merged.items()
            },
            trace_id=context.trace_id,
            span_id=context.span_id,
        )
    )
