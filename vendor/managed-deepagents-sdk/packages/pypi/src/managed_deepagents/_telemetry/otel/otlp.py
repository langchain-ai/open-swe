"""The OTLP/HTTP JSON shapes the log collection endpoint accepts.

The subset of ``ExportLogsServiceRequest`` this runtime sends. Field names
follow the OTLP JSON encoding (lowerCamelCase; 64-bit integers as decimal
strings).
"""

from __future__ import annotations

from typing import TypedDict


class OtlpAnyValue(TypedDict, total=False):
    """Exactly one of the value fields is set."""

    stringValue: str
    boolValue: bool
    intValue: str
    doubleValue: float


class OtlpKeyValue(TypedDict):
    key: str
    value: OtlpAnyValue


class OtlpLogRecord(TypedDict):
    """One record, carrying its own context's trace and span ids."""

    timeUnixNano: str
    severityNumber: int
    severityText: str
    body: OtlpAnyValue
    attributes: list[OtlpKeyValue]
    traceId: str
    spanId: str


class OtlpScope(TypedDict):
    name: str


class OtlpScopeLogs(TypedDict):
    scope: OtlpScope
    logRecords: list[OtlpLogRecord]


class OtlpResource(TypedDict):
    attributes: list[OtlpKeyValue]


class OtlpResourceLogs(TypedDict):
    resource: OtlpResource
    scopeLogs: list[OtlpScopeLogs]


class ExportLogsServiceRequest(TypedDict):
    resourceLogs: list[OtlpResourceLogs]
