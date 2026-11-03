"""Why a model call failed, as the code a failure reply reads, and its detail for logs.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
tells "provider overloaded" from every other ``APIError`` is made where the call
failed, while the exception is intact, and left on the thread for the reply to read.
"""

import json
from typing import Any, Final

_MAX_BODY_CHARS: Final = 2000

# Thread-metadata key holding the classification of the run's last model error,
# written while the exception is intact and read back by the completion webhook.
LAST_MODEL_ERROR_KEY: Final = "last_model_error"

# Exception class names, the only failure signal the run-completion webhook gets:
# the platform serializes the class name but scrubs the message of any exception
# type outside its allowlist.
_ERROR_TYPE_CODES: Final = {
    "RateLimitError": "provider_rate_limited",
    "OverloadedError": "provider_overloaded",
    "APIConnectionError": "provider_unavailable",
    "APIError": "provider_unavailable",
    "APIStatusError": "provider_unavailable",
    "InternalServerError": "provider_unavailable",
    "APITimeoutError": "provider_timeout",
    "ModelCallTimeoutError": "provider_timeout",
    "GraphRecursionError": "step_limit",
    "RecursionError": "step_limit",
    "SandboxConnectionError": "sandbox_unreachable",
    "SandboxUnreachableError": "sandbox_unreachable",
    "BackendUnavailableError": "backend_interrupted",
    "BackendTimeoutError": "backend_interrupted",
    # Not raised: the stalled-run sweep names it when it fails a run that went silent.
    "RunStalledError": "run_stalled",
}


def code_for_error_type(error_type: str | None) -> str | None:
    """Failure code for a bare exception class name."""
    return _ERROR_TYPE_CODES.get(error_type) if error_type else None


def classify_exception(exc: BaseException) -> str | None:
    """Failure code for a live exception, using status and provider error body.

    Provider-agnostic on purpose: a gateway can surface an overload as anything
    from a 529 to a bare ``APIError`` carrying ``overloaded_error`` in its body.
    """
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    provider_code = ""
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            provider_code = str(inner.get("type") or inner.get("code") or "")
    haystack = f"{provider_code} {exc}".lower()

    if status == 429 or "rate_limit" in haystack:
        return "provider_rate_limited"
    if status == 529 or "overloaded" in haystack:
        return "provider_overloaded"
    if "context_length_exceeded" in haystack or "prompt is too long" in haystack:
        return "context_too_long"
    if "model_not_available" in haystack or "model_not_found" in haystack:
        return "model_unavailable"
    if isinstance(status, int) and status >= 500:
        return "provider_unavailable"
    return code_for_error_type(type(exc).__name__)


def exception_fields(exc: BaseException) -> dict[str, Any]:
    """Class, message, and any HTTP status / request id / response body on ``exc``.

    Provider SDKs put the useful part of a failure (``overloaded_error``, a
    rate-limit window, a rejected model id) in the response body, which
    ``str(exc)`` frequently omits. For logs only: the body is bounded, never shown.
    """
    try:
        fields: dict[str, Any] = {"error_type": type(exc).__name__, "error_message": str(exc)}
        status = getattr(exc, "status_code", None)
        if status is not None:
            fields["status"] = status
        request_id = getattr(exc, "request_id", None)
        if request_id:
            fields["request_id"] = request_id
        body = getattr(exc, "body", None)
        if body is not None:
            fields["body"] = _bounded(body)
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            fields["cause"] = f"{type(cause).__name__}: {cause}"
        return fields
    except Exception:  # noqa: BLE001
        return {"error_type": type(exc).__name__, "error_message": repr(exc)}


def _bounded(body: Any) -> Any:
    try:
        rendered = json.dumps(body, default=str)
    except TypeError, ValueError:
        rendered = str(body)
    if len(rendered) <= _MAX_BODY_CHARS:
        return body
    return rendered[:_MAX_BODY_CHARS]
