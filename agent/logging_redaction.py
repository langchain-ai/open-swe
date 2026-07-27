from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

_TOKEN_RE = re.compile(r"(token=)[^&#]*", re.IGNORECASE)
_REDACTED_LOGGERS = ("httpx", "langgraph_api.webhook", "langgraph_api.server", "asgi")
_FILTER_MARKER = "_open_swe_webhook_token_redaction"


def _contains_token(value: object, *, render_objects: bool) -> bool:
    if isinstance(value, str):
        return "token=" in value.lower()
    if isinstance(value, Mapping):
        return any(_contains_token(item, render_objects=render_objects) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_token(item, render_objects=render_objects) for item in value)
    return render_objects and value is not None and "token=" in str(value).lower()


def _redact_text(value: str) -> str:
    return _TOKEN_RE.sub(r"\1***", value)


def _redact_value(value: Any, *, render_objects: bool) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: _redact_value(item, render_objects=render_objects) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, render_objects=render_objects) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, render_objects=render_objects) for item in value)
    if render_objects and value is not None:
        rendered = str(value)
        if "token=" in rendered.lower():
            return _redact_text(rendered)
    return value


def _record_contains_token(record: logging.LogRecord) -> bool:
    return _contains_token(
        record.msg, render_objects=not isinstance(record.msg, Mapping)
    ) or _contains_token(record.args, render_objects=True)


def _redact_record_payload(
    record: logging.LogRecord,
) -> tuple[object, tuple[object, ...] | Mapping[str, object] | None]:
    if isinstance(record.msg, Mapping):
        return (
            _redact_value(record.msg, render_objects=False),
            _redact_value(record.args, render_objects=True),
        )
    if isinstance(record.msg, str) and "token=" in record.msg.lower():
        return _redact_text(record.getMessage()), ()
    return (
        _redact_value(record.msg, render_objects=True),
        _redact_value(record.args, render_objects=True),
    )


def _replace_with_safe_placeholder(record: logging.LogRecord) -> None:
    if isinstance(record.msg, Mapping):
        record.msg = {
            "event": "token redaction failed",
            "logger": record.name,
            "level": record.levelname.lower(),
        }
    else:
        record.msg = "token redaction failed"
    record.args = ()


class WebhookTokenRedactionFilter(logging.Filter):
    """Redact webhook query tokens before third-party log records are rendered."""

    _open_swe_webhook_token_redaction = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            contains_token = _record_contains_token(record)
        except Exception:
            _replace_with_safe_placeholder(record)
            return True
        if not contains_token:
            return True
        try:
            msg, args = _redact_record_payload(record)
        except Exception:
            _replace_with_safe_placeholder(record)
            return True
        record.msg = msg
        record.args = args
        return True


_FILTER = WebhookTokenRedactionFilter()


def install_webhook_token_redaction() -> None:
    """Install token redaction on each third-party logger that can expose it."""
    for name in _REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(getattr(item, _FILTER_MARKER, False) for item in logger.filters):
            logger.addFilter(_FILTER)
