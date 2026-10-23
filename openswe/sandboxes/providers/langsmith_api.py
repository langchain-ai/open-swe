"""This deployment's LangSmith sandbox APIs, for the routes the langsmith SDK does not model.

Boxes and snapshots may live in two LangSmiths: the deployment's own, where everything
created before ``NEW_SANDBOX_REGION_ENDPOINT`` was set stays, and the new sandbox region,
where new ones are created. Each records the API root it lives at; an unrecorded one is
the deployment's.

Kept apart from the LangSmith provider so the dashboard can call the API without
importing the agent stack the provider's sandbox backend pulls in.
"""

from typing import TYPE_CHECKING, Literal, Self

import httpx2

from openswe.config import ENV
from openswe.utils.http_pool import pooled_transport

if TYPE_CHECKING:
    from langsmith.sandbox import AsyncSandboxClient

_ERROR_BODY_CHARS = 500
_SANDBOX_API_SUFFIX = "/v2/sandboxes"


def _api_root(url: str) -> str:
    return url.rstrip("/").removesuffix(_SANDBOX_API_SUFFIX)


class UnknownSandboxAPIError(RuntimeError):
    """A box or snapshot lives at a LangSmith this deployment holds no key for."""

    def __init__(self, api_root: str) -> None:
        super().__init__(
            f"No LangSmith API key is configured for {api_root}, where this sandbox lives; "
            "it is neither LANGSMITH_ENDPOINT nor the new sandbox region's NEW_SANDBOX_REGION_ENDPOINT"
        )


class SandboxAPI:
    """Where the LangSmith API is, and the key this deployment calls it with."""

    def __init__(self, root: str, api_key: str | None) -> None:
        self.api_root = _api_root(root)
        self.api_key = api_key

    @classmethod
    def new_sandbox_region(cls) -> Self:
        """The new sandbox region, where new boxes and snapshots are created.

        Without one configured it is the deployment's own LangSmith. Its endpoint and key
        are set together: either alone would pair one LangSmith's key with another's API.
        """
        endpoint = ENV.NEW_SANDBOX_REGION_ENDPOINT.optional()
        api_key = ENV.NEW_SANDBOX_REGION_API_KEY.optional()
        if endpoint and api_key:
            return cls(endpoint, api_key)
        if endpoint or api_key:
            msg = "Set both NEW_SANDBOX_REGION_ENDPOINT and NEW_SANDBOX_REGION_API_KEY, or neither"
            raise ValueError(msg)
        return cls.deployment()

    @classmethod
    def deployment(cls) -> Self:
        """The deployment's own LangSmith, where MDA opens each thread's browser."""
        return cls(ENV.LANGSMITH_ENDPOINT.get(), ENV.LANGSMITH_API_KEY.optional())

    @classmethod
    def at(cls, api_root: str | None) -> Self:
        """The API a box or snapshot recorded at ``api_root`` lives at.

        ``None`` is one recorded before regions were: it lives in the deployment's own.
        """
        if api_root is None:
            return cls.deployment()
        for api in (cls.new_sandbox_region(), cls.deployment()):
            if api.api_root == _api_root(api_root):
                return api
        raise UnknownSandboxAPIError(api_root)

    def holds(self, api_root: str | None) -> bool:
        """Whether a box or snapshot recorded at ``api_root`` lives at this API."""
        recorded = _api_root(api_root) if api_root else SandboxAPI.deployment().api_root
        return recorded == self.api_root

    @property
    def is_deployment(self) -> bool:
        """Whether this is the deployment's own LangSmith, the one MDA runs in too."""
        return self.api_root == SandboxAPI.deployment().api_root

    @property
    def jwks_url(self) -> str:
        """Where an app in one of this API's boxes verifies the identity token LangSmith forwards.

        The keys are served by the API host, not by the app origin the token names as
        its issuer.
        """
        return f"{self.api_root}/.well-known/jwks.json"

    def client(self) -> AsyncSandboxClient:
        """The langsmith SDK's sandbox client for this API."""
        from langsmith.sandbox import AsyncSandboxClient

        return AsyncSandboxClient(api_key=self.api_key, api_endpoint=self.endpoint)

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
            msg = "LangSmith API key not set"
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
