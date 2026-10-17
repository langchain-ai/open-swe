"""Tests for safe runtime logging primitives."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from managed_deepagents._log import logger


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
