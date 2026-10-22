"""This deployment's LangSmith sandbox API, for the routes the langsmith SDK does not model.

Kept apart from the LangSmith provider so the dashboard can call the API without
importing the agent stack the provider's sandbox backend pulls in.
"""

from typing import Literal, Self

import httpx2

from openswe.config import ENV
from openswe.utils.http_pool import pooled_transport

_ERROR_BODY_CHARS = 500
_SANDBOX_API_SUFFIX = "/v2/sandboxes"


class SandboxAPI:
    """Where the LangSmith API is, and the key this deployment calls it with."""

    def __init__(self, root: str, api_key: str | None) -> None:
        self.api_root = root.rstrip("/").removesuffix(_SANDBOX_API_SUFFIX)
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> Self:
        """The API ``LANGSMITH_ENDPOINT`` names, with ``LANGSMITH_API_KEY``."""
        return cls(ENV.LANGSMITH_ENDPOINT.get(), ENV.LANGSMITH_API_KEY.optional())

    @property
    def endpoint(self) -> str:
        """The sandbox API base, as the langsmith SDK clients take it.

        The SDK's ``api_endpoint`` is the sandbox base (root + ``/v2/sandboxes``),
        not the API root, and its methods append ``/boxes``, ``/snapshots``, etc.
        """
        return f"{self.api_root}{_SANDBOX_API_SUFFIX}"

    def headers(self) -> dict[str, str]:
        """Headers that authenticate this deployment to the sandbox API, sockets included."""
        if not self.api_key:
            msg = "LANGSMITH_API_KEY not set"
            raise ValueError(msg)
        return {"X-API-Key": self.api_key}

    async def request(
        self,
        method: Literal["GET", "POST", "PATCH", "DELETE"],
        path: str,
        *,
        json: dict[str, object] | None = None,
        timeout: float,
    ) -> httpx2.Response:
        """Call the sandbox API at ``path`` (under ``/v2/sandboxes``)."""
        async with httpx2.AsyncClient(transport=pooled_transport(), timeout=timeout) as client:
            response = await client.request(
                method, f"{self.endpoint}{path}", json=json, headers=self.headers()
            )
        self.raise_for_status(response)
        return response

    @staticmethod
    def raise_for_status(response: httpx2.Response) -> None:
        """``raise_for_status``, carrying the API's own explanation of a rejection.

        httpx builds its message from the status line and an MDN link only, so
        without the body the reason never reaches the logs.
        """
        try:
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            body = exc.response.text.strip()[:_ERROR_BODY_CHARS]
            if not body:
                raise
            raise httpx2.HTTPStatusError(
                f"{exc}\nResponse body: {body}", request=exc.request, response=exc.response
            ) from exc
