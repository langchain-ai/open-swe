"""Tests for the internal OTLP log context and exporter."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import TYPE_CHECKING

import httpx

from managed_deepagents._resilience import RetryPolicy
from managed_deepagents._telemetry.otel import (
    TRACE_ID_METADATA_KEY,
    LogContext,
    LogRecord,
    current_log_context,
    enter_log_context,
    exit_log_context,
    record_log,
    set_log_attributes,
    trace_id_from_metadata,
    trace_id_from_traceparent,
)
from managed_deepagents._telemetry.otel.exporter import (
    MAX_RECORDS_PER_REQUEST,
    MAX_REQUEST_BYTES,
    create_log_sink,
    encode_logs,
    resolve_otel_log_config,
    set_log_sink_for_tests,
    to_otlp_log_record,
)
from managed_deepagents._telemetry.otel.truncate import MAX_VALUE_BYTES, truncate_middle

if TYPE_CHECKING:
    from tests.conftest import CapturedLogs


class CapturingDisabledSink:
    enabled = False

    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def push(self, record: LogRecord) -> None:
        self.records.append(record)

    def idle(self, timeout: float) -> bool:
        return True

    def shutdown(self, timeout: float) -> None:
        pass


TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
DEPLOYED = {
    "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
    "LANGSMITH_API_KEY": "lsv2_test",
    "LANGSMITH_TENANT_ID": "tenant-1",
    "LANGSMITH_ENDPOINT": "https://api.smith.example.com",
}


def _within(context: LogContext, fn) -> None:  # noqa: ANN001 - test helper
    token = enter_log_context(context)
    try:
        fn()
    finally:
        exit_log_context(token)


SPAN_ID = "00f067aa0ba902b7"


def _record(body: str, *, trace_id: str = TRACE_ID, attributes: dict | None = None) -> LogRecord:
    return LogRecord(
        uid=f"uid-{body}",
        time_unix_nano=1_700_000_000_000_000_000,
        severity="INFO",
        body=body,
        attributes=attributes or {},
        trace_id=trace_id,
        span_id=SPAN_ID,
    )


def test_takes_the_trace_id_from_a_valid_traceparent() -> None:
    assert trace_id_from_traceparent(f"00-{TRACE_ID}-00f067aa0ba902b7-01") == TRACE_ID


def test_mints_a_trace_id_for_a_missing_malformed_or_all_zero_traceparent() -> None:
    for header in (None, "", "garbage", f"00-{'0' * 32}-00f067aa0ba902b7-01"):
        trace_id = trace_id_from_traceparent(header)
        assert len(trace_id) == 32
        assert trace_id != "0" * 32


def test_reads_the_trace_id_handed_over_in_run_metadata() -> None:
    assert trace_id_from_metadata({TRACE_ID_METADATA_KEY: TRACE_ID}) == TRACE_ID
    assert len(trace_id_from_metadata({TRACE_ID_METADATA_KEY: "nope"})) == 32
    assert len(trace_id_from_metadata(None)) == 32


def test_records_nothing_outside_a_context(captured_logs: CapturedLogs) -> None:
    record_log("INFO", "lost")
    assert current_log_context() is None
    assert captured_logs.records == []


def test_queues_each_record_under_the_contexts_ids(captured_logs: CapturedLogs) -> None:
    context = LogContext(trace_id=TRACE_ID)
    _within(context, lambda: record_log("INFO", "hello"))

    assert len(captured_logs.records) == 1
    record = captured_logs.records[0]
    assert (record.body, record.trace_id, record.span_id) == ("hello", TRACE_ID, context.span_id)


def test_gives_every_record_its_own_uid(captured_logs: CapturedLogs) -> None:
    def body() -> None:
        record_log("INFO", "first")
        record_log("INFO", "second")

    _within(LogContext(trace_id=TRACE_ID), body)

    first, second = (record.uid for record in captured_logs.records)
    assert str(uuid.UUID(first)) == first
    assert first != second


def test_attaches_the_attributes_current_when_each_record_was_made(
    captured_logs: CapturedLogs,
) -> None:
    context = LogContext(trace_id=TRACE_ID, attributes={"channel": "slack"})

    def body() -> None:
        record_log("INFO", "before")
        set_log_attributes({"run_id": "run-1", "ignored": None})
        record_log("INFO", "after", attributes={"stage": "join"})

    _within(context, body)

    assert [record.attributes for record in captured_logs.records] == [
        {"channel": "slack"},
        {"channel": "slack", "run_id": "run-1", "stage": "join"},
    ]


def test_keeps_the_errors_type_message_and_stack(captured_logs: CapturedLogs) -> None:
    context = LogContext(trace_id=TRACE_ID)
    error = TypeError("boom")
    _within(context, lambda: record_log("ERROR", "failed", error=error))

    attributes = captured_logs.records[0].attributes
    assert attributes["exception.type"] == "TypeError"
    assert attributes["exception.message"] == "boom"
    assert "TypeError: boom" in str(attributes["exception.stacktrace"])


class _ExplodingTracebackError(Exception):
    """Formatting the traceback, as exception attributes do, raises."""

    @property
    def __traceback__(self) -> None:  # type: ignore[override]
        raise AssertionError("exception attributes were built")


def test_builds_nothing_when_the_sink_is_disabled() -> None:
    sink = CapturingDisabledSink()
    set_log_sink_for_tests(sink)
    try:
        _within(
            LogContext(trace_id=TRACE_ID),
            lambda: record_log("ERROR", "failed", error=_ExplodingTracebackError("boom")),
        )
    finally:
        set_log_sink_for_tests(None)

    assert sink.records == []


def test_cuts_the_middle_of_a_body_or_string_attribute_over_the_value_limit(
    captured_logs: CapturedLogs,
) -> None:
    long = f"head{'x' * MAX_VALUE_BYTES}tail"
    _within(
        LogContext(trace_id=TRACE_ID),
        lambda: record_log("ERROR", long, attributes={"output": long, "attempt": 2}),
    )

    record = captured_logs.records[0]
    for value in (record.body, str(record.attributes["output"])):
        assert len(value.encode()) <= MAX_VALUE_BYTES
        assert value.startswith("head")
        assert value.endswith("tail")
    assert record.attributes["attempt"] == 2


def test_truncate_middle_leaves_a_value_within_the_limit_whole() -> None:
    value = "é" * (MAX_VALUE_BYTES // 2)
    assert truncate_middle(value) == value


def test_truncate_middle_keeps_both_ends_with_the_size_of_the_cut() -> None:
    value = f"start\n{'é' * 40_000}\nImportError: the cause"

    truncated = truncate_middle(value)

    assert len(truncated.encode()) <= MAX_VALUE_BYTES
    assert truncated.startswith("start\n")
    assert truncated.endswith("\nImportError: the cause")
    marker = re.search(r"\n… (\d+) bytes omitted …\n", truncated)
    assert marker is not None
    assert len(truncated.encode()) - len(marker.group(0).encode()) + int(marker.group(1)) == len(
        value.encode()
    )


def test_keeps_contexts_apart_across_concurrent_tasks(captured_logs: CapturedLogs) -> None:
    async def run(trace_id: str) -> LogContext:
        context = LogContext(trace_id=trace_id)
        token = enter_log_context(context)
        try:
            await asyncio.sleep(0)
            record_log("INFO", trace_id)
        finally:
            exit_log_context(token)
        return context

    async def main() -> tuple[LogContext, LogContext]:
        return await asyncio.gather(run("a" * 32), run("b" * 32))

    a, b = asyncio.run(main())

    by_trace = {record.trace_id: record for record in captured_logs.records}
    assert (by_trace[a.trace_id].body, by_trace[a.trace_id].span_id) == ("a" * 32, a.span_id)
    assert (by_trace[b.trace_id].body, by_trace[b.trace_id].span_id) == ("b" * 32, b.span_id)
    assert a.span_id != b.span_id


def test_targets_the_collection_endpoint_with_the_deployments_key() -> None:
    config = resolve_otel_log_config(DEPLOYED)

    assert config is not None
    assert config.url == "https://api.smith.example.com/v1/platform/mda/otel/v1/logs"
    assert config.headers["x-api-key"] == "lsv2_test"
    assert config.headers["x-tenant-id"] == "tenant-1"


def test_sends_nothing_when_opted_out_local_keyless_or_outside_a_deployment() -> None:
    for env in (
        {**DEPLOYED, "MDA_TELEMETRY_DISABLED": "1"},
        {**DEPLOYED, "DO_NOT_TRACK": "true"},
        {**DEPLOYED, "MDA_LOCAL_DEV": "1"},
        {**DEPLOYED, "LANGSMITH_API_KEY": ""},
        {k: v for k, v in DEPLOYED.items() if k != "LANGSMITH_HOST_PROJECT_ID"},
    ):
        assert resolve_otel_log_config(env) is None


def test_sends_the_key_over_plain_http_only_to_this_machine() -> None:
    def at(endpoint: str) -> object:
        return resolve_otel_log_config({**DEPLOYED, "LANGSMITH_ENDPOINT": endpoint})

    assert at("http://localhost:1984") is not None
    assert at("http://127.0.0.1:1984") is not None
    assert at("http://[::1]:1984") is not None
    assert at("http://langsmith.example.com") is None


def test_builds_an_otlp_json_logs_request_whose_records_keep_their_trace() -> None:
    record = _record(
        "failed", attributes={"run_id": "run-1", "attempt": 2, "retry": False, "ratio": 0.5}
    )
    record.severity = "ERROR"

    encoded = encode_logs([to_otlp_log_record(record)], "1.2.3")
    resource_logs = encoded["resourceLogs"][0]

    assert resource_logs["resource"]["attributes"] == [
        {"key": "service.name", "value": {"stringValue": "mda-python"}},
        {"key": "service.version", "value": {"stringValue": "1.2.3"}},
    ]
    encoded_record = resource_logs["scopeLogs"][0]["logRecords"][0]
    assert encoded_record["severityNumber"] == 17
    assert encoded_record["body"] == {"stringValue": "failed"}
    assert encoded_record["traceId"] == TRACE_ID
    assert encoded_record["spanId"] == SPAN_ID
    assert encoded_record["timeUnixNano"].isdigit()
    assert encoded_record["attributes"] == [
        {"key": "run_id", "value": {"stringValue": "run-1"}},
        {"key": "attempt", "value": {"intValue": "2"}},
        {"key": "retry", "value": {"boolValue": False}},
        {"key": "ratio", "value": {"doubleValue": 0.5}},
        {"key": "log.record.uid", "value": {"stringValue": "uid-failed"}},
    ]


NO_RETRY = RetryPolicy(
    max_attempts=1, base_delay_seconds=0, max_delay_seconds=0, retry_on=lambda _e: False
)


def _capturing() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    return httpx.MockTransport(handler), requests


def _record_batches(requests: list[httpx.Request]) -> list[list[dict[str, object]]]:
    return [
        json.loads(request.content)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        for request in requests
    ]


def test_batches_records_from_many_contexts_into_requests_the_endpoint_accepts() -> None:
    transport, requests = _capturing()
    sink = create_log_sink(DEPLOYED, transport=transport, retry=NO_RETRY)

    for i in range(MAX_RECORDS_PER_REQUEST):
        sink.push(_record(f"r{i}"))
    sink.push(_record("other run", trace_id="b" * 32))
    sink.shutdown(timeout=2)

    batches = _record_batches(requests)
    assert sum(len(batch) for batch in batches) == MAX_RECORDS_PER_REQUEST + 1
    assert all(len(batch) <= MAX_RECORDS_PER_REQUEST for batch in batches)
    assert batches[-1][-1]["traceId"] == "b" * 32
    assert requests[0].headers["x-api-key"] == "lsv2_test"


def test_splits_large_records_into_requests_under_the_byte_limit() -> None:
    transport, requests = _capturing()
    sink = create_log_sink(DEPLOYED, transport=transport, retry=NO_RETRY)
    body = "x" * 30_000

    for _ in range(MAX_RECORDS_PER_REQUEST):
        sink.push(_record(body, attributes={"output": body}))
    sink.shutdown(timeout=5)

    batches = _record_batches(requests)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == MAX_RECORDS_PER_REQUEST
    assert all(len(request.content) < MAX_REQUEST_BYTES + 1_024 for request in requests)


def test_drops_records_without_sending_when_logs_must_not_be_sent() -> None:
    transport, requests = _capturing()
    sink = create_log_sink({**DEPLOYED, "MDA_TELEMETRY_DISABLED": "1"}, transport=transport)

    sink.push(_record("r0"))
    sink.shutdown(timeout=2)

    assert requests == []


def test_retries_a_rate_limited_batch_and_drops_a_rejected_one() -> None:
    statuses = [429, 200, 404]
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        status = statuses[len(seen)] if len(seen) < len(statuses) else 200
        seen.append(status)
        return httpx.Response(status, headers={"retry-after": "0"} if status == 429 else {})

    sink = create_log_sink(DEPLOYED, transport=httpx.MockTransport(handler), max_wait_seconds=0.05)

    sink.push(_record("r0"))
    assert sink.idle(timeout=2)
    sink.push(_record("r1"))
    assert sink.idle(timeout=2)

    assert seen == [429, 200, 404]


def test_sends_a_retried_record_with_the_uid_it_was_first_sent_with() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200)

    retry = RetryPolicy(
        max_attempts=2, base_delay_seconds=0, max_delay_seconds=0, retry_on=lambda _e: True
    )
    sink = create_log_sink(
        DEPLOYED, transport=httpx.MockTransport(handler), retry=retry, max_wait_seconds=0.05
    )

    sink.push(_record("r0"))
    assert sink.idle(timeout=2)

    uids = [
        next(a["value"] for a in batch[0]["attributes"] if a["key"] == "log.record.uid")
        for batch in _record_batches(requests)
    ]
    assert uids == [{"stringValue": "uid-r0"}, {"stringValue": "uid-r0"}]


def test_push_never_blocks_or_raises_when_the_endpoint_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    sink = create_log_sink(
        DEPLOYED, transport=httpx.MockTransport(handler), retry=NO_RETRY, max_wait_seconds=0.05
    )
    started = time.monotonic()

    sink.push(_record("r0"))

    assert time.monotonic() - started < 0.1
    assert sink.idle(timeout=2)
