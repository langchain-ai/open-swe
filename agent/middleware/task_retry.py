from __future__ import annotations

import json

_RETURN_TO_MODEL_CODES = frozenset({"invalid_prompt", "context_length_exceeded"})
_RETURN_TO_MODEL_STATUS_CODES = frozenset({400, 422})
_RETRY_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_TRANSIENT_ERROR_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "TransportError",
    }
)


def _error_body(exc: Exception) -> dict[str, object]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        return nested if isinstance(nested, dict) else body
    return {}


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _error_fields(exc: Exception) -> dict[str, object]:
    body = _error_body(exc)
    out: dict[str, object] = {}
    status = _status_code(exc)
    if status is not None:
        out["status_code"] = status
    for key in ("type", "code", "message"):
        value = body.get(key)
        if not isinstance(value, str) or not value:
            value = getattr(exc, key, None)
        if isinstance(value, str) and value:
            out[key] = value
    return out


def _is_httpx_transport_error(exc: Exception) -> bool:
    try:
        import httpx
    except ImportError:  # pragma: no cover - dependency is declared in production
        return False
    return isinstance(exc, httpx.TransportError)


def task_retry_on(exc: Exception) -> bool:
    status = _status_code(exc)
    if isinstance(status, int) and (status in _RETRY_HTTP_STATUS_CODES or status >= 500):
        return True
    return exc.__class__.__name__ in _TRANSIENT_ERROR_NAMES or _is_httpx_transport_error(exc)


def task_on_failure(exc: Exception) -> str:
    error = _error_fields(exc)
    code = error.get("code")
    status = error.get("status_code")
    returnable = code in _RETURN_TO_MODEL_CODES or (
        code is None
        and error.get("type") == "invalid_request_error"
        and isinstance(status, int)
        and status in _RETURN_TO_MODEL_STATUS_CODES
    )
    if not returnable:
        raise exc
    return json.dumps(
        {"status": "failed", "source": "subagent", "error": error},
        sort_keys=True,
    )
