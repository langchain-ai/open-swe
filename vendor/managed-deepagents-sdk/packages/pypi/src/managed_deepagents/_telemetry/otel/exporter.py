"""The process-wide log sink for the LangSmith MDA log collection endpoint.

Records from every log context go as OTLP/HTTP JSON, which the endpoint
forwards to Datadog.

Built on our own ``BatchProcessor`` and ``with_retry`` rather than the OTel
SDK. PostHog events need the same batching and retry, so both sinks share one
mechanism, and the Node runtime mirrors it.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING, Final, Protocol

import httpx

from managed_deepagents._agent_auth_http import agent_auth_headers
from managed_deepagents._concurrency import BatchProcessor
from managed_deepagents._http_status import HttpStatusError, is_retryable, requested_delay
from managed_deepagents._loopback import sends_key_securely
from managed_deepagents._resilience import RetryPolicy, with_retry
from managed_deepagents._telemetry.gates import is_telemetry_disabled
from managed_deepagents._telemetry.lifecycle import on_shutdown
from managed_deepagents._telemetry.metadata import package_version

if TYPE_CHECKING:
    from managed_deepagents._concurrency import Deadline
    from managed_deepagents._telemetry.otel.context import (
        LogAttributes,
        LogAttributeValue,
        LogRecord,
    )
    from managed_deepagents._telemetry.otel.otlp import (
        ExportLogsServiceRequest,
        OtlpAnyValue,
        OtlpKeyValue,
        OtlpLogRecord,
    )

_LOGS_PATH: Final = "/v1/platform/mda/otel/v1/logs"
_SERVICE_NAME: Final = "mda-python"
#: Bounds one export request; the OTel exporters' default.
_EXPORT_TIMEOUT_SECONDS: Final = 10.0
#: How long a partial batch waits for more records.
_BATCH_WAIT_SECONDS: Final = 1.0
#: Records waiting to send; later ones are dropped.
_MAX_QUEUED_RECORDS: Final = 10_000
_FIRST_ERROR_STATUS: Final = 300
#: The collection endpoint accepts at most this many records per request.
MAX_RECORDS_PER_REQUEST: Final = 200
#: Encoded records per request, under the endpoint's 5MB body limit.
MAX_REQUEST_BYTES: Final = 4 << 20

#: OTel semantic-convention attribute for a log record's unique id.
_RECORD_UID_KEY: Final = "log.record.uid"

_SEVERITY_NUMBER: Final = {"INFO": 9, "WARN": 13, "ERROR": 17}


@dataclass(frozen=True)
class OtelLogConfig:
    url: str
    headers: dict[str, str]


def _nonempty(value: str | None) -> str | None:
    return (value or "").strip() or None


def resolve_otel_log_config(env: Mapping[str, str] | None = None) -> OtelLogConfig | None:
    """Where and how to send logs, or ``None`` when logs must not be sent.

    Nothing is sent when telemetry is off, the process is not a LangSmith
    deployment, there is no key, or the key would cross the network in cleartext.
    """
    from managed_deepagents._connections import resolve_agent_auth_base_url  # noqa: PLC0415

    resolved = os.environ if env is None else env
    if is_telemetry_disabled(resolved):
        return None
    if resolved.get("MDA_LOCAL_DEV") == "1" or not _nonempty(
        resolved.get("LANGSMITH_HOST_PROJECT_ID")
    ):
        return None
    api_key = _nonempty(resolved.get("LANGSMITH_API_KEY")) or _nonempty(
        resolved.get("LANGCHAIN_API_KEY")
    )
    if api_key is None:
        return None
    try:
        origin = resolve_agent_auth_base_url(resolved)
    except Exception:  # noqa: BLE001 - optional telemetry cannot fail on bad config
        return None
    url = f"{origin.rstrip('/')}{_LOGS_PATH}"
    if not sends_key_securely(url):
        return None
    return OtelLogConfig(
        url=url,
        headers=agent_auth_headers(
            api_key=api_key,
            workspace_id=_nonempty(resolved.get("LANGSMITH_TENANT_ID"))
            or _nonempty(resolved.get("LANGSMITH_WORKSPACE_ID")),
        ),
    )


def _any_value(value: LogAttributeValue) -> OtlpAnyValue:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def _key_values(attributes: LogAttributes) -> list[OtlpKeyValue]:
    return [{"key": key, "value": _any_value(value)} for key, value in attributes.items()]


def to_otlp_log_record(record: LogRecord) -> OtlpLogRecord:
    return {
        "timeUnixNano": str(record.time_unix_nano),
        "severityNumber": _SEVERITY_NUMBER[record.severity],
        "severityText": record.severity,
        "body": {"stringValue": record.body},
        "attributes": [
            *_key_values(record.attributes),
            {"key": _RECORD_UID_KEY, "value": {"stringValue": record.uid}},
        ],
        "traceId": record.trace_id,
        "spanId": record.span_id,
    }


def _encoded_size(record: OtlpLogRecord) -> int:
    return len(json.dumps(record).encode())


def encode_logs(
    records: list[OtlpLogRecord], service_version: str | None = None
) -> ExportLogsServiceRequest:
    """One OTLP ``ExportLogsServiceRequest``.

    Records from many contexts can share a request; each keeps its own trace
    and span ids.
    """
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": _key_values(
                        {
                            "service.name": _SERVICE_NAME,
                            "service.version": service_version or package_version(),
                        }
                    )
                },
                "scopeLogs": [{"scope": {"name": "managed-deepagents"}, "logRecords": records}],
            }
        ]
    }


#: 429 and 5xx (including timeouts and network failures) are tried again.
EXPORT_RETRY: Final = RetryPolicy(
    max_attempts=5,
    base_delay_seconds=1.0,
    max_delay_seconds=30.0,
    retry_on=is_retryable,
    delay_for=requested_delay,
)


class LogSink(Protocol):
    """Where records go once built. Never blocks or raises."""

    @property
    def enabled(self) -> bool:
        """Whether pushed records go anywhere; when not, callers skip building them."""
        ...

    def push(self, record: LogRecord) -> None: ...

    def idle(self, timeout: float) -> bool:
        """Wait up to ``timeout`` until every pushed record is handled."""
        ...

    def shutdown(self, timeout: float) -> None:
        """Refuse new records and drain what is queued for up to ``timeout``."""
        ...


class _NoopLogSink:
    enabled = False

    def push(self, record: LogRecord) -> None:
        pass

    def idle(self, timeout: float) -> bool:  # noqa: ARG002 - nothing to wait for
        return True

    def shutdown(self, timeout: float) -> None:
        pass


class ExportingLogSink:
    """Batches records in the background and sends them to the collection endpoint."""

    enabled = True

    def __init__(
        self,
        config: OtelLogConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        max_wait_seconds: float = _BATCH_WAIT_SECONDS,
        retry: RetryPolicy = EXPORT_RETRY,
    ) -> None:
        # One client for the sink's life, so batches reuse its connection.
        self._client = httpx.Client(
            verify=ssl.create_default_context(),
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            follow_redirects=False,
            transport=transport,
        )
        self._processor: BatchProcessor[OtlpLogRecord] = BatchProcessor(
            handle=with_retry(partial(self._post, config), retry),
            max_batch_size=MAX_RECORDS_PER_REQUEST,
            max_wait_seconds=max_wait_seconds,
            max_queued=_MAX_QUEUED_RECORDS,
            max_batch_bytes=MAX_REQUEST_BYTES,
            size_of=_encoded_size,
            name="mda-otel-logs",
        )

    def push(self, record: LogRecord) -> None:
        self._processor.push(to_otlp_log_record(record))

    def idle(self, timeout: float) -> bool:
        return self._processor.idle(timeout)

    def shutdown(self, timeout: float) -> None:
        self._processor.close(timeout)
        self._client.close()

    def _post(self, config: OtelLogConfig, batch: list[OtlpLogRecord], deadline: Deadline) -> None:
        response = self._client.post(
            config.url,
            headers=config.headers,
            json=encode_logs(batch),
            timeout=min(_EXPORT_TIMEOUT_SECONDS, deadline.remaining()),
        )
        if response.status_code >= _FIRST_ERROR_STATUS:
            raise HttpStatusError.from_response(
                response.status_code, response.headers.get("retry-after")
            )


def create_log_sink(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    max_wait_seconds: float = _BATCH_WAIT_SECONDS,
    retry: RetryPolicy = EXPORT_RETRY,
) -> LogSink:
    """A sink for the collection endpoint, or one that drops everything.

    Records are dropped when :func:`resolve_otel_log_config` returns nothing.
    """
    config = resolve_otel_log_config(env)
    if config is None:
        return _NoopLogSink()
    return ExportingLogSink(
        config, transport=transport, max_wait_seconds=max_wait_seconds, retry=retry
    )


_sink: LogSink | None = None
_sink_lock = threading.Lock()


def log_sink() -> LogSink:
    """The process-wide sink, created on first use.

    When it exports, it drains at process shutdown.
    """
    global _sink  # noqa: PLW0603 - process-wide sink, created once
    sink = _sink
    if sink is not None:
        return sink
    with _sink_lock:
        if _sink is None:
            _sink = create_log_sink()
            if isinstance(_sink, ExportingLogSink):
                on_shutdown(_sink.shutdown)
        return _sink


def set_log_sink_for_tests(override: LogSink | None) -> None:
    """Test-only sink override; ``None`` restores the default."""
    global _sink  # noqa: PLW0603 - mirrors the process-wide sink
    with _sink_lock:
        _sink = override
