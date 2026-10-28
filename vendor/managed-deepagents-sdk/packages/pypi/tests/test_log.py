"""Tests for safe runtime logging primitives."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from managed_deepagents._log import logger

if TYPE_CHECKING:
    from tests.conftest import CapturedLogs


def test_logger_namespaces_levels_and_formats_lazily(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="mda"):
        logger.info("started %s", "run")
        logger.warning("degraded")
        logger.error("failed")

    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("INFO", "[mda] started run"),
        ("WARNING", "[mda] degraded"),
        ("ERROR", "[mda] failed"),
    ]


def test_safe_error_label_never_includes_the_message() -> None:
    error = RuntimeError("user message")
    assert logger.safe_error_label(error) == "RuntimeError"


def test_safe_error_label_reads_bounded_http_statuses() -> None:
    class ResponseError(RuntimeError):
        response = SimpleNamespace(status_code=502)

    error = ResponseError("response body")
    assert logger.safe_error_label(error) == "ResponseError HTTP 502"

    error.response.status_code = 999
    assert logger.safe_error_label(error) == "ResponseError"


def test_log_identifier_sanitizes_and_bounds_untrusted_values() -> None:
    assert logger.is_log_printable("\u2028") is False
    assert logger.is_log_printable(" ") is True
    assert logger.log_identifier("ok\u2028forged\u202e") == "ok?forged?"
    assert logger.log_identifier("delivery café") == "delivery café"
    assert logger.log_identifier("x" * 129) == f"{'x' * 125}..."


def test_logger_queues_lines_inside_a_log_context_with_detail_stdout_never_sees(
    caplog: pytest.LogCaptureFixture, captured_logs: CapturedLogs
) -> None:
    from managed_deepagents._telemetry.otel import (  # noqa: PLC0415
        LogContext,
        enter_log_context,
        exit_log_context,
    )

    context = LogContext(trace_id="a" * 32)
    token = enter_log_context(context)
    try:
        with caplog.at_level(logging.ERROR, logger="mda"):
            logger.error(
                "run %s failed",
                "r1",
                error=RuntimeError("agent server body"),
                attributes={"run_id": "run-1"},
            )
    finally:
        exit_log_context(token)

    assert [record.getMessage() for record in caplog.records] == ["[mda] run r1 failed"]
    assert len(captured_logs.records) == 1
    record = captured_logs.records[0]
    assert record.severity == "ERROR"
    assert record.body == "run r1 failed"
    assert record.attributes["run_id"] == "run-1"
    assert record.attributes["exception.type"] == "RuntimeError"
    assert record.attributes["exception.message"] == "agent server body"
