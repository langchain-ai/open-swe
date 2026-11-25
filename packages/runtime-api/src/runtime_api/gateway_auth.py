"""Gateway calls that ask for their credential on every request.

A run's model calls go through the LangSmith LLM Gateway as whoever the run acts for:
a person's LangSmith OAuth token, or the deployment's LangSmith API key for system
work. The gateway takes either as ``Authorization: Bearer``. OAuth access tokens
last minutes, far shorter than a run, so each SDK here asks for the token again
before every request instead of holding a key.

The provider SDKs wired by ``GatewayBearer`` stay the caller's dependencies.
"""

import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from dataclasses import dataclass

import anthropic
import httpx
import httpx2
import openai
from fireworks import AsyncFireworks
from fireworks import DefaultAsyncHttpxClient as FireworksAsyncHttpxClient

from runtime_api.models import GATEWAY_MODEL_ROUTE_HEADER, GatewayModelRoute

type GatewayToken = Callable[[], Awaitable[str]]
"""The bearer credential for one gateway request, asked for again before each one."""

# SDK constructors insist on a key; the bearer auth replaces it on every request.
PLACEHOLDER_API_KEY = "langsmith-gateway"
_PROVIDER_KEY_HEADERS = ("x-api-key", "x-goog-api-key")


@dataclass(frozen=True)
class GatewayFailure:
    """A credential or access rejection, independent of the provider SDK."""

    status: int
    message: str

    @classmethod
    def of(cls, error: BaseException) -> GatewayFailure | None:
        """Read provider status errors and their wrapped causes without looping."""
        seen: set[int] = set()
        while id(error) not in seen:
            seen.add(id(error))
            status = getattr(error, "status_code", None)
            if not isinstance(status, int):
                status = getattr(error, "code", None)
            message = f"{error} {getattr(error, 'body', '')}".lower()
            if isinstance(status, int) and (
                status in {401, 402, 403}
                or (status == 424 and "workspace secret" in message and "missing" in message)
                or (status == 503 and "credential validation failed" in message)
            ):
                return cls(status, message)
            cause = error.__cause__ or error.__context__
            if cause is None:
                break
            error = cause
        return None

    @property
    def access_message(self) -> str:
        """Describe remediation without echoing provider bodies or credential material."""
        if self.status == 402:
            if "blocked by gateway policies" in self.message:
                return (
                    "The model gateway blocked this request because a spending limit was reached. "
                    "Ask a LangSmith organization admin to review the gateway's cost policies "
                    "and usage before retrying. Sending the same request again will not "
                    "resolve the limit."
                )
            return (
                "The model gateway blocked this request under a billing or usage policy. "
                "Ask a LangSmith organization admin to review gateway policies and billing before retrying."
            )
        if self.status == 403:
            return (
                "The model gateway denied access to this model. Ask a LangSmith organization "
                "admin to review gateway access policies and the workspace's provider permissions."
            )
        if self.status == 424:
            secret = re.search(
                r"\b(?:OPENAI|ANTHROPIC|GOOGLE|GEMINI|FIREWORKS|BASETEN)_API_KEY\b",
                self.message.upper(),
            )
            name = f" ({secret.group()})" if secret else ""
            return (
                f"The model gateway is missing a required workspace provider secret{name}. "
                "Ask a LangSmith workspace admin to add it in workspace secrets before retrying."
            )
        return (
            "The model gateway could not validate the credentials for this model. "
            "Ask a LangSmith workspace admin to check the workspace's provider API key "
            "and gateway credentials. If your LangSmith login expired, sign in to Robin again."
        )


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
        """``init_chat_model`` kwargs that authenticate ``provider``'s SDK this way."""
        match provider:
            case "anthropic":
                return {
                    "api_key": PLACEHOLDER_API_KEY,
                    "anthropic_proxy": None,
                    "http_async_client": anthropic.DefaultAsyncHttpxClient(
                        timeout=timeout, auth=_Httpx2BearerAuth(self)
                    ),
                }
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
