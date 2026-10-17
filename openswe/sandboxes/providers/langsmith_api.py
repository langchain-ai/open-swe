"""Authenticated calls to the LangSmith sandbox API, for routes the langsmith SDK does not model.

Kept apart from the LangSmith provider so the dashboard can call the API without
importing the agent stack the provider's sandbox backend pulls in.
"""

from typing import Any, Literal

import httpx2

from openswe.config import ENV
from openswe.utils.http_pool import pooled_transport

_ERROR_BODY_CHARS = 500
_SANDBOX_API_SUFFIX = "/v2/sandboxes"


def langsmith_api_key() -> str | None:
    """The deployment's LangSmith API key (``LANGSMITH_API_KEY``), as the rest of the app reads it."""
    return ENV.LANGSMITH_API_KEY.optional()


def langsmith_endpoint() -> str:
    """The deployment's LangSmith API root, used for sandbox operations."""
    return ENV.LANGSMITH_ENDPOINT.get()


def langsmith_api_root() -> str:
    """The LangSmith API root, without any sandbox suffix the endpoint was configured with."""
    root = langsmith_endpoint().rstrip("/")
    return root[: -len(_SANDBOX_API_SUFFIX)] if root.endswith(_SANDBOX_API_SUFFIX) else root


def sandbox_api_endpoint() -> str:
    """Sandbox API base URL for the langsmith SDK clients.

    The SDK's ``api_endpoint`` is the sandbox base (root + ``/v2/sandboxes``),
    not the API root, and its methods append ``/boxes``, ``/snapshots``, etc.
    """
    return f"{langsmith_api_root()}{_SANDBOX_API_SUFFIX}"


def with_response_body(exc: BaseException) -> httpx2.HTTPStatusError | None:
    """Re-raisable copy of ``exc`` carrying the response body, or ``None`` to re-raise as-is.

    ``raise_for_status`` builds its message from the status line and an MDN link
    only, so the API's own explanation of a rejection never reaches the logs.
    """
    if not isinstance(exc, httpx2.HTTPStatusError):
        return None
    body = exc.response.text.strip()[:_ERROR_BODY_CHARS]
    if not body:
        return None
    return httpx2.HTTPStatusError(
        f"{exc}\nResponse body: {body}",
        request=exc.request,
        response=exc.response,
    )


def sandbox_api_headers() -> dict[str, str]:
    """Headers that authenticate this deployment to the sandbox API, sockets included."""
    api_key = langsmith_api_key()
    if not api_key:
        msg = "LANGSMITH_API_KEY not set"
        raise ValueError(msg)
    return {"X-API-Key": api_key}


async def sandbox_api_request(
    method: Literal["GET", "POST", "PATCH", "DELETE"],
    path: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: float,
) -> httpx2.Response:
    """Call the sandbox API at ``path`` (under ``/v2/sandboxes``) as this deployment.

    Raises ``httpx2.HTTPStatusError`` carrying the API's own explanation when it refuses.
    """
    async with httpx2.AsyncClient(transport=pooled_transport(), timeout=timeout) as client:
        response = await client.request(
            method,
            f"{sandbox_api_endpoint()}{path}",
            json=json,
            headers=sandbox_api_headers(),
        )
    try:
        response.raise_for_status()
    except httpx2.HTTPStatusError as exc:
        enriched = with_response_body(exc)
        if enriched is not None:
            raise enriched from exc
        raise
    return response
