"""Retries a synchronous subagent's ``task`` call through a provider's passing failures.

A subagent has no fallback model, so retrying the delegated task is how it
recovers; an error the model can fix, such as a prompt too long, is its result.
"""

import json

from langchain.agents.middleware import ToolRetryMiddleware

from rt.model_calls import RETRYABLE_STATUS_CODES, is_transient

_RETURN_TO_MODEL_CODES = frozenset({"invalid_prompt", "context_length_exceeded"})
_RETURN_TO_MODEL_STATUS_CODES = frozenset({400, 422})
# Connection failures of providers whose SDKs ``is_transient`` doesn't know.
_OTHER_SDK_TRANSIENT_NAMES = frozenset({"APIConnectionError", "APITimeoutError"})


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


def task_retry_on(exc: Exception) -> bool:
    """Whether the delegated task may pass on another try: what a model call retries, and
    any provider's passing HTTP failure."""
    if is_transient(exc):
        return True
    status = _status_code(exc)
    if isinstance(status, int) and (status in RETRYABLE_STATUS_CODES or status >= 500):
        return True
    return exc.__class__.__name__ in _OTHER_SDK_TRANSIENT_NAMES


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


def task_retry() -> ToolRetryMiddleware:
    return ToolRetryMiddleware(
        max_retries=2,
        tools=["task"],
        retry_on=task_retry_on,
        on_failure=task_on_failure,
        initial_delay=1.0,
        max_delay=10.0,
    )
