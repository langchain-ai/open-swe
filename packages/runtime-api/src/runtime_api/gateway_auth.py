"""Gateway calls that ask for their credential on every request.

A run's model calls go through the LangSmith LLM Gateway as whoever the run acts for:
a person's LangSmith OAuth token, or the deployment's LangSmith API key for system
work. The gateway takes either as ``Authorization: Bearer``. OAuth access tokens
last minutes, far shorter than a run, so each SDK here asks for the token again
before every request instead of holding a key.

Only ``runtime_api.models`` imports this module, and only for a model built with a
``GatewayBearer``, so the provider SDKs it wires stay the caller's dependencies.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from functools import cached_property

import anthropic
import httpx
import httpx2
import openai
from fireworks import AsyncFireworks
from fireworks import DefaultAsyncHttpxClient as FireworksAsyncHttpxClient
from langchain_anthropic import ChatAnthropic
from pydantic import ConfigDict, Field

from runtime_api.models import GATEWAY_MODEL_ROUTE_HEADER, GatewayModelRoute

type GatewayToken = Callable[[], Awaitable[str]]
"""The bearer credential for one gateway request, asked for again before each one."""

# SDK constructors insist on a key; the bearer auth replaces it on every request.
PLACEHOLDER_API_KEY = "langsmith-gateway"
_PROVIDER_KEY_HEADERS = ("x-api-key", "x-goog-api-key")


class GatewayBearer:
    """Resolve the credential and model route just before each gateway request."""

    def __init__(
        self,
        token: GatewayToken,
        *,
        route: Callable[[], Awaitable[GatewayModelRoute]] | None = None,
    ) -> None:
        self.token = token
        self.route = route

    async def authorize(self, headers: httpx.Headers | httpx2.Headers) -> None:
        token = await self.token()
        for name in _PROVIDER_KEY_HEADERS:
            headers.pop(name, None)
        headers["Authorization"] = f"Bearer {token}"
        headers[GATEWAY_MODEL_ROUTE_HEADER] = (
            await self.route() if self.route is not None else "default"
        )

    def sdk_overrides(
        self, provider: str, base_url: str, *, timeout: float | None
    ) -> dict[str, object]:
        """``init_chat_model`` kwargs that authenticate ``provider``'s SDK this way.

        Anthropic needs ``GatewayChatAnthropic`` as well, which ``chat_anthropic`` builds.
        """
        match provider:
            case "openai" | "baseten":
                return {
                    "api_key": PLACEHOLDER_API_KEY,
                    "http_async_client": openai.DefaultAsyncHttpxClient(
                        timeout=timeout, auth=_Httpx2BearerAuth(self)
                    ),
                }
            case "fireworks":
                # ChatFireworks keeps a client it is given; it retries itself, so the SDK must not.
                client = AsyncFireworks(
                    api_key=PLACEHOLDER_API_KEY,
                    base_url=base_url,
                    timeout=timeout,
                    max_retries=0,
                    http_client=FireworksAsyncHttpxClient(auth=_HttpxBearerAuth(self)),
                )
                return {"api_key": PLACEHOLDER_API_KEY, "async_client": client.chat.completions}
            case "google_genai":
                # An explicit transport makes google-genai use httpx, and so this auth, over aiohttp.
                return {
                    "api_key": PLACEHOLDER_API_KEY,
                    "client_args": {
                        "auth": _HttpxBearerAuth(self),
                        "transport": httpx.AsyncHTTPTransport(),
                    },
                }
            case _:
                return {"api_key": PLACEHOLDER_API_KEY}

    def authenticates(self, model_kwargs: dict[str, object]) -> bool:
        """Whether ``sdk_overrides`` set up these kwargs, so the model was routed this way."""
        return model_kwargs.get("api_key") == PLACEHOLDER_API_KEY

    def chat_anthropic(self, model: str, kwargs: dict[str, object]) -> ChatAnthropic:
        return GatewayChatAnthropic.model_validate({**kwargs, "model": model, "gateway": self})


class _HttpxBearerAuth(httpx.Auth):
    """Fireworks' and google-genai's clients (httpx)."""

    def __init__(self, bearer: GatewayBearer) -> None:
        self._bearer = bearer

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        raise NotImplementedError("Gateway bearer auth is async-only")

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await self._bearer.authorize(request.headers)
        yield request


class _Httpx2BearerAuth(httpx2.Auth):
    """OpenAI's and Anthropic's clients (httpx2)."""

    def __init__(self, bearer: GatewayBearer) -> None:
        self._bearer = bearer

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response]:
        raise NotImplementedError("Gateway bearer auth is async-only")

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        await self._bearer.authorize(request.headers)
        yield request


class GatewayChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` whose requests carry the gateway token asked for just before each.

    A subclass rather than a wrapper, so code that checks for ``ChatAnthropic`` (prompt
    caching, thinking sanitizers) still sees one.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gateway: GatewayBearer = Field(exclude=True)

    @cached_property
    def _client(self) -> anthropic.Client:
        raise NotImplementedError("Gateway bearer auth is async-only")

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        params = self._client_params
        return anthropic.AsyncClient(
            **params,
            http_client=anthropic.DefaultAsyncHttpxClient(
                base_url=params["base_url"],
                timeout=params.get("timeout"),
                auth=_Httpx2BearerAuth(self.gateway),
            ),
        )
