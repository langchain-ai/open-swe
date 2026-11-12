"""Why a model call failed, as the code a failure reply reads, and its detail for logs.

The run-completion webhook only ever sees the exception's class name (the platform
scrubs the message of any type outside its allowlist), so the classification that
tells "provider overloaded" from every other ``APIError`` is made where the call
failed, while the exception is intact, and left on the thread for the reply to read.
"""

import json
from collections.abc import Mapping
from typing import Any, Final

_MAX_BODY_CHARS: Final = 2000

# Thread-metadata key holding the classification of the run's last model error,
# written while the exception is intact and read back by the completion webhook.
LAST_MODEL_ERROR_KEY: Final = "last_model_error"

# The ``response_metadata`` key marking an answer the runtime wrote for a model call it
# could not make (``outage`` or ``model_access``). It is final: the loop posts it as it
# is instead of asking the model again for a reply it cannot give.
MODEL_FAILURE_REPLY_KEY: Final = "open_swe_model_failure"

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


def is_model_failure_reply(message: object) -> bool:
    """Whether ``message`` is the runtime's answer for a model call it could not make."""
    metadata = getattr(message, "response_metadata", None)
    return isinstance(metadata, Mapping) and bool(metadata.get(MODEL_FAILURE_REPLY_KEY))


def code_for_error_type(error_type: str | None) -> str | None:
    """Failure code for a bare exception class name."""
    return _ERROR_TYPE_CODES.get(error_type) if error_type else None


def classify_exception(exc: BaseException) -> str | None:
    """Failure code for a live exception, using status and provider error body."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    provider_code = ""
    if isinstance(body, dict):
        inner = body.get("error")
        error = inner if isinstance(inner, dict) else body
        provider_code = str(error.get("type") or error.get("code") or "")
    haystack = f"{provider_code} {exc}".lower()

    if status == 429 or "rate_limit" in haystack:
        return "provider_rate_limited"
    if status == 529 or "overloaded" in haystack:
        return "provider_overloaded"
    if "context_length_exceeded" in haystack or "prompt is too long" in haystack:
        return "context_too_long"
    if "model_not_available" in haystack or "model_not_found" in haystack:
        return "model_unavailable"
    if provider_code == "server_error" or (isinstance(status, int) and status >= 500):
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
