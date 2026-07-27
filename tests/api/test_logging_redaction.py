from __future__ import annotations

import importlib
import io
import json
import logging
import logging.config
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import structlog
from langgraph_api.logging import Formatter as LangGraphFormatter

from agent import logging_redaction


@contextmanager
def _capture(name: str, formatter: logging.Formatter) -> Iterator[io.StringIO]:
    logger = logging.getLogger(name)
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield stream
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


@pytest.fixture(autouse=True)
def _restore_filters() -> Iterator[None]:
    names = ("httpx", "langgraph_api.webhook", "langgraph_api.server", "asgi")
    original = {name: logging.getLogger(name).filters[:] for name in names}
    yield
    for name, filters in original.items():
        logging.getLogger(name).filters = filters


def _install() -> None:
    logging_redaction.install_webhook_token_redaction()


def _structlog_formatter() -> logging.Formatter:
    return LangGraphFormatter(None, None, "%")


def test_fastapi_import_path_installs_filters() -> None:
    importlib.reload(importlib.import_module("agent.api.app"))

    for name in ("httpx", "langgraph_api.webhook", "langgraph_api.server", "asgi"):
        assert any(
            getattr(item, logging_redaction._FILTER_MARKER, False)
            for item in logging.getLogger(name).filters
        )


def test_graphs_package_import_installs_filters() -> None:
    importlib.reload(importlib.import_module("agent.graphs"))

    for name in ("httpx", "langgraph_api.webhook", "langgraph_api.server", "asgi"):
        assert any(
            getattr(item, logging_redaction._FILTER_MARKER, False)
            for item in logging.getLogger(name).filters
        )


def test_every_dispatchable_graph_is_covered_by_graphs_package() -> None:
    config = json.loads((Path(__file__).parents[2] / "langgraph.json").read_text())
    for name, target in config["graphs"].items():
        module = target.split(":", 1)[0]
        assert module.startswith("agent.graphs."), (
            f"graph {name!r} entrypoint {module} is outside agent.graphs; "
            "token redaction would not install in its worker process"
        )


def test_httpx_url_is_redacted_through_logging_machinery() -> None:
    token_value = "'LEAK outbound value\""
    _install()

    with _capture("httpx", logging.Formatter("%(levelname)s %(name)s %(message)s")) as stream:
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "POST",
            httpx.URL(f"https://example.test/webhooks/run-complete?token={token_value}"),
            "HTTP/1.1",
            200,
            "OK",
        )

    output = stream.getvalue()
    assert "LEAK" not in output
    assert "token=***" in output
    assert (
        'HTTP Request: POST https://example.test/webhooks/run-complete?token=*** "HTTP/1.1 200 OK"'
        in output
    )


def test_webhook_structlog_success_and_failure_fields_are_redacted() -> None:
    token_value = "'LEAK worker value\""
    url = f"https://example.test/webhooks/run-complete?token={token_value}"
    _install()
    logger = structlog.stdlib.get_logger("langgraph_api.webhook")

    with _capture("langgraph_api.webhook", _structlog_formatter()) as stream:
        logger.info("Background worker called webhook", webhook=url, run_id="run-1")
        logger.exception(
            f"Background worker failed to call webhook {url}",
            exc_info=RuntimeError(f"request failed for {url}"),
            webhook=url,
            run_id="run-2",
        )

    output = stream.getvalue()
    assert "LEAK" not in output
    assert output.count("token=***") == 4
    assert "Background worker called webhook" in output
    assert "Background worker failed to call webhook" in output
    assert "run-1" in output
    assert "run-2" in output


def test_asgi_access_log_query_string_is_redacted_and_fields_are_preserved() -> None:
    token_value = "'LEAK inbound value\""
    _install()
    logger = structlog.stdlib.get_logger("asgi")

    with _capture("asgi", _structlog_formatter()) as stream:
        logger.warning(
            "POST /webhooks/run-complete 401 3ms",
            method="POST",
            path="/webhooks/run-complete",
            status=401,
            route="/webhooks/run-complete",
            query_string=f"token={token_value}",
        )

    output = stream.getvalue()
    assert "LEAK" not in output
    assert "token=***" in output
    assert output.count("POST") == 2
    assert output.count("/webhooks/run-complete") == 3
    assert output.count("401") == 2


def test_logger_filter_survives_handler_reconfiguration_and_install_is_idempotent() -> None:
    logger = logging.getLogger("httpx")
    _install()
    _install()
    redaction_filters = [
        item for item in logger.filters if getattr(item, logging_redaction._FILTER_MARKER, False)
    ]
    assert len(redaction_filters) == 1

    old_handler = logging.StreamHandler(io.StringIO())
    logger.addHandler(old_handler)
    configurator = logging.config.DictConfigurator({"version": 1})
    configurator.common_logger_config(logger, {"handlers": []}, incremental=False)

    assert redaction_filters[0] in logger.filters
    assert old_handler not in logger.handlers

    token_value = "d" * 64
    with _capture("httpx", logging.Formatter("%(message)s")) as stream:
        logger.info("GET https://example.test/?token=%s", token_value)
    assert token_value not in stream.getvalue()
    assert "token=***" in stream.getvalue()


def test_redaction_failure_never_emits_token_bearing_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_value = "e" * 64
    _install()

    def fail(_record: logging.LogRecord) -> tuple[object, object]:
        raise RuntimeError("redaction failed")

    monkeypatch.setattr(logging_redaction, "_redact_record_payload", fail)

    with _capture("httpx", logging.Formatter("%(name)s %(levelname)s %(message)s")) as stream:
        logging.getLogger("httpx").info("ordinary request completed")
        logging.getLogger("httpx").warning("GET https://example.test/?token=%s", token_value)

    lines = stream.getvalue().splitlines()
    assert lines == [
        "httpx INFO ordinary request completed",
        "httpx WARNING token redaction failed",
    ]
    assert token_value not in stream.getvalue()


def test_scan_failure_uses_safe_placeholder_before_later_token_argument() -> None:
    token_value = "0" * 64
    _install()

    class Unrenderable:
        def __str__(self) -> str:
            raise RuntimeError("cannot render")

    with _capture("httpx", logging.Formatter("%(name)s %(levelname)s %(message)s")) as stream:
        logging.getLogger("httpx").warning("%s %s", Unrenderable(), f"token={token_value}")

    assert stream.getvalue() == "httpx WARNING token redaction failed\n"
    assert token_value not in stream.getvalue()


def test_structlog_redaction_failure_uses_renderable_safe_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_value = "f" * 64
    _install()

    def fail(_record: logging.LogRecord) -> tuple[object, object]:
        raise RuntimeError("redaction failed")

    monkeypatch.setattr(logging_redaction, "_redact_record_payload", fail)

    with _capture("asgi", _structlog_formatter()) as stream:
        structlog.stdlib.get_logger("asgi").warning(
            "POST /webhooks/run-complete 401 3ms",
            query_string=f"token={token_value}",
        )

    output = stream.getvalue()
    assert token_value not in output
    assert "token redaction failed" in output
    assert "asgi" in output
    assert "warning" in output
