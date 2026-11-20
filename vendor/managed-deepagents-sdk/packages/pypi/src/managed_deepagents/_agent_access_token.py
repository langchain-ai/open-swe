from __future__ import annotations

import asyncio
import contextvars
import hmac
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from http import HTTPStatus
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    import httpx2

from managed_deepagents._agent_auth_http import _decode_json_object, agent_auth_client
from managed_deepagents._connections import (
    InvalidAgentAuthEndpointError,
    resolve_agent_auth_base_url,
)
from managed_deepagents._http_status import HttpStatusError
from managed_deepagents._langsmith_env import _first_set, resolve_platform_injected_api_key
from managed_deepagents._log import logger

AGENT_ACCESS_TOKEN_HEADER = "X-LangSmith-Agent-Access-Token"  # noqa: S105

_POD_TOKEN_PATH_ENV = "LANGSMITH_AGENT_AUTH_POD_TOKEN_PATH"  # noqa: S105
_GATEWAY_AUDIENCE_ENV = "LANGSMITH_AGENT_ACCESS_TOKEN_GATEWAY_AUDIENCE"
_AGENT_AUTH_PATH = "/v1/agent-auth"
_MANAGED_TOOLS_PATH = "/v1/managed-tools"
_AGENT_AUTH_API_PATHS = (f"{_AGENT_AUTH_PATH}/", f"{_MANAGED_TOOLS_PATH}/")
_TOKEN_PATH = f"{_AGENT_AUTH_PATH}/token"
_TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105
_POD_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"  # noqa: S105
_MAX_TOKEN_LIFETIME_SECONDS = 300
_RENEW_AFTER_LIFETIME_FRACTION = 0.8
_RETRY_AFTER_FAILURE_SECONDS = 5
_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5


class _Url(Protocol):
    @property
    def scheme(self) -> str: ...
    @property
    def host(self) -> str: ...
    @property
    def port(self) -> int | None: ...
    @property
    def path(self) -> str: ...


class _OutgoingRequest(Protocol):
    @property
    def url(self) -> _Url: ...
    @property
    def headers(self) -> MutableMapping[str, str]: ...


class _RefusedError(Exception):
    def __init__(self, reason: str, retry_after: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


@dataclass(frozen=True)
class _IssuedToken:
    access_token: str = field(repr=False)
    expires_in: int


@dataclass(frozen=True)
class _Token:
    value: str = field(repr=False)
    renew_at: float
    expires_at: float

    @classmethod
    def issued(cls, token: _IssuedToken, now: float) -> _Token:
        return cls(
            value=token.access_token,
            renew_at=now + token.expires_in * _RENEW_AFTER_LIFETIME_FRACTION,
            expires_at=now + token.expires_in,
        )


@dataclass(frozen=True)
class _Claim:
    token: Future[str | None]
    mint: Future[str | None] | None = None

    @classmethod
    def settled(cls, value: str | None, mint: Future[str | None] | None = None) -> _Claim:
        token: Future[str | None] = Future()
        token.set_result(value)
        return cls(token, mint)


class _Backoff:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._retry_at: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    def holds(self, audience: str, now: float) -> bool:
        with self._lock:
            return now < self._retry_at.get(audience, 0.0)

    def start(self, audience: str, now: float, refusal: _RefusedError) -> None:
        with self._lock:
            failures = self._failures.get(audience, 0) + 1
            self._failures[audience] = failures
            self._retry_at[audience] = now + _wait_after(refusal, failures)

    def reset(self, audience: str) -> None:
        with self._lock:
            self._failures.pop(audience, None)


@dataclass(frozen=True)
class AgentAccessTokenSettings:
    pod_token_path: Path
    agent_auth_url: str
    deployment_key: str = field(repr=False)
    gateway_audience: str | None

    @property
    def managed_tools_audience(self) -> str:
        return f"{self.agent_auth_url}{_MANAGED_TOOLS_PATH}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentAccessTokenSettings | None:
        pod_token_path = _first_set(env, _POD_TOKEN_PATH_ENV)
        if pod_token_path is None:
            return None
        try:
            agent_auth_url = resolve_agent_auth_base_url(env)
        except InvalidAgentAuthEndpointError:
            logger.warning(
                "Agent access tokens are off: the LangSmith endpoint must be a scheme and host"
                " only, with no path, query or credentials"
            )
            return None
        if urlsplit(agent_auth_url).scheme != "https":
            logger.warning("Agent access tokens are off: the LangSmith endpoint is not HTTPS")
            return None
        return cls(
            pod_token_path=Path(pod_token_path),
            agent_auth_url=agent_auth_url,
            deployment_key=resolve_platform_injected_api_key(env),
            gateway_audience=_gateway_audience(env),
        )


class AgentAccessTokens:
    def __init__(
        self,
        settings: AgentAccessTokenSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = agent_auth_client(transport)
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens: dict[str, _Token] = {}
        self._minting: dict[str, Future[str | None]] = {}
        self._backoff = _Backoff()
        gateway = settings.gateway_audience
        self._gateway_origin = _origin(httpx.URL(gateway)) if gateway is not None else None
        self._agent_auth_origin = _origin(httpx.URL(settings.agent_auth_url))

    @property
    def signs_gateway_calls(self) -> bool:
        return self._gateway_origin is not None

    def token(self, audience: str) -> str | None:
        claim = self._claim(audience)
        mint = claim.mint
        if mint is not None:
            if mint is claim.token:
                self._mint(audience, mint)
            else:
                self._mint_in_background(audience, mint)
        return claim.token.result()

    async def atoken(self, audience: str) -> str | None:
        claim = self._claim(audience)
        if claim.mint is not None:
            self._mint_in_background(audience, claim.mint)
        return await asyncio.wrap_future(claim.token)

    async def amanaged_tools_token(self, api_key: str | None, url: str) -> str | None:
        audience = self._managed_tools_audience_for(api_key, url)
        return await self.atoken(audience) if audience is not None else None

    def sign_gateway_call(self, request: _OutgoingRequest) -> None:
        audience = self._gateway_audience_for(request)
        if audience is not None:
            _replace_token(request, self.token(audience))

    async def asign_gateway_call(self, request: _OutgoingRequest) -> None:
        audience = self._gateway_audience_for(request)
        if audience is not None:
            _replace_token(request, await self.atoken(audience))

    def _claim(self, audience: str) -> _Claim:
        with self._lock:
            now = self._clock()
            cached = self._tokens.get(audience)
            if cached is not None and now < cached.renew_at:
                return _Claim.settled(cached.value)
            unexpired = _unexpired(cached, now)
            minting = self._minting.get(audience)
            if minting is not None:
                return _Claim.settled(unexpired) if unexpired else _Claim(minting)
            if self._backoff.holds(audience, now):
                return _Claim.settled(unexpired)
            mint: Future[str | None] = Future()
            mint.set_running_or_notify_cancel()
            self._minting[audience] = mint
            return _Claim.settled(unexpired, mint) if unexpired else _Claim(mint, mint)

    def _mint_in_background(self, audience: str, mint: Future[str | None]) -> None:
        # Its own thread, not the loop's executor: a caller cancelled while
        # queued there would leave the mint never started and later callers waiting.
        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(self._mint, audience, mint),
            name="agent-access-token-mint",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            self._finish_mint(audience, mint)

    def _mint(self, audience: str, token: Future[str | None]) -> None:
        try:
            token.set_result(self._renewed(audience))
        finally:
            self._finish_mint(audience, token)

    def _finish_mint(self, audience: str, token: Future[str | None]) -> None:
        with self._lock:
            del self._minting[audience]
        if not token.done():
            token.set_result(None)

    def _renewed(self, audience: str) -> str | None:
        now = self._clock()
        try:
            issued = self._issue(audience)
        except Exception as error:  # noqa: BLE001 - a token must never fail the call it signs
            self._backoff.start(audience, self._clock(), _logged_refusal(error, audience))
            # Only callers without a valid token wait on a mint, so a failed one gives them none.
            return None
        self._backoff.reset(audience)
        with self._lock:
            self._tokens[audience] = _Token.issued(issued, now)
        return issued.access_token

    def _managed_tools_audience_for(self, api_key: str | None, url: str) -> str | None:
        audience = self._settings.managed_tools_audience
        if not url.startswith(f"{audience}/") or not self._is_deployment_key(api_key or ""):
            return None
        return audience

    def _gateway_audience_for(self, request: _OutgoingRequest) -> str | None:
        url = request.url
        if (
            url.scheme != "https"
            or _origin(url) != self._gateway_origin
            or self._is_agent_auth_api(url)
        ):
            return None
        return self._settings.gateway_audience

    def _is_agent_auth_api(self, url: _Url) -> bool:
        return _origin(url) == self._agent_auth_origin and url.path.startswith(
            _AGENT_AUTH_API_PATHS
        )

    def _is_deployment_key(self, presented: str) -> bool:
        key = self._settings.deployment_key
        return bool(key) and hmac.compare_digest(presented.encode(), key.encode())

    def _issue(self, audience: str) -> _IssuedToken:
        form = {
            "grant_type": _TOKEN_EXCHANGE_GRANT_TYPE,
            "subject_token": self._pod_token(),
            "subject_token_type": _POD_TOKEN_TYPE,
            "resource": audience,
        }
        try:
            response = self._client.post(
                f"{self._settings.agent_auth_url}{_TOKEN_PATH}",
                data=form,
                timeout=_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise _RefusedError(type(error).__name__, _RETRY_AFTER_FAILURE_SECONDS) from None
        if response.is_redirect:
            raise _RefusedError("redirect", _RETRY_AFTER_FAILURE_SECONDS)
        if response.status_code != HTTPStatus.OK:
            raise _refusal(response)
        return _issued_token(response)

    def _pod_token(self) -> str:
        try:
            return self._settings.pod_token_path.read_text().strip()
        except (OSError, UnicodeDecodeError):
            raise _RefusedError("pod_token_unreadable", _MAX_TOKEN_LIFETIME_SECONDS) from None


class _ProcessTokens:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resolved = False
        self._tokens: AgentAccessTokens | None = None

    def get(self) -> AgentAccessTokens | None:
        if not self._resolved:
            with self._lock:
                if not self._resolved:
                    self._use(_tokens_from_env())
        return self._tokens

    def set(self, tokens: AgentAccessTokens | None) -> None:
        with self._lock:
            self._use(tokens)

    def _use(self, tokens: AgentAccessTokens | None) -> None:
        self._tokens = tokens
        self._resolved = True


_process_tokens = _ProcessTokens()
_signing_sends: set[object] = set()


def agent_access_tokens() -> AgentAccessTokens | None:
    return _process_tokens.get()


def set_agent_access_tokens_for_tests(tokens: AgentAccessTokens | None) -> None:
    _process_tokens.set(tokens)


def sign_gateway_calls() -> None:
    tokens = agent_access_tokens()
    if tokens is not None and tokens.signs_gateway_calls:
        _wrap_send(httpx.Client, httpx.AsyncClient)
        # Model SDKs moved to httpx2 (openai 3, anthropic 1); langsmith installs it.
        if find_spec("httpx2") is not None:
            import httpx2  # noqa: PLC0415

            _wrap_send(httpx2.Client, httpx2.AsyncClient)


async def amanaged_tools_token_headers(api_key: str | None, url: str) -> dict[str, str]:
    tokens = agent_access_tokens()
    token = await tokens.amanaged_tools_token(api_key, url) if tokens is not None else None
    return _token_header(token)


def _tokens_from_env() -> AgentAccessTokens | None:
    settings = AgentAccessTokenSettings.from_env(os.environ)
    return AgentAccessTokens(settings) if settings is not None else None


def _wrap_send(
    client: type[httpx.Client | httpx2.Client],
    async_client: type[httpx.AsyncClient | httpx2.AsyncClient],
) -> None:
    if client.send in _signing_sends:
        return
    send = cast("Callable[..., object]", client.send)
    asend = cast("Callable[..., Awaitable[object]]", async_client.send)

    def signing_send(self: object, request: _OutgoingRequest, **kwargs: object) -> object:
        tokens = agent_access_tokens()
        if tokens is not None:
            tokens.sign_gateway_call(request)
        return send(self, request, **kwargs)

    async def signing_asend(self: object, request: _OutgoingRequest, **kwargs: object) -> object:
        tokens = agent_access_tokens()
        if tokens is not None:
            await tokens.asign_gateway_call(request)
        return await asend(self, request, **kwargs)

    setattr(client, "send", signing_send)  # noqa: B010
    setattr(async_client, "send", signing_asend)  # noqa: B010
    _signing_sends.update((signing_send, signing_asend))


def _token_header(token: str | None) -> dict[str, str]:
    return {AGENT_ACCESS_TOKEN_HEADER: token} if token else {}


def _wait_after(refusal: _RefusedError, failures: int) -> float:
    growing = _RETRY_AFTER_FAILURE_SECONDS * 2 ** (failures - 1)
    return min(max(refusal.retry_after, growing), _MAX_TOKEN_LIFETIME_SECONDS)


def _replace_token(request: _OutgoingRequest, token: str | None) -> None:
    request.headers.pop(AGENT_ACCESS_TOKEN_HEADER, None)
    if token:
        request.headers[AGENT_ACCESS_TOKEN_HEADER] = token


def _origin(url: _Url) -> tuple[str, str, int | None]:
    return (url.scheme, url.host, url.port)


def _gateway_audience(env: Mapping[str, str]) -> str | None:
    audience = _first_set(env, _GATEWAY_AUDIENCE_ENV)
    if audience is None:
        return None
    try:
        httpx.URL(audience)
    except httpx.InvalidURL:
        logger.warning("Agent access tokens skip Gateway calls: the Gateway audience is not a URL")
        return None
    return audience


def _unexpired(token: _Token | None, now: float) -> str | None:
    return token.value if token is not None and now < token.expires_at else None


def _issued_token(response: httpx.Response) -> _IssuedToken:
    body = _decode_json_object(response.content)
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        raise _RefusedError("token_response_without_token", _RETRY_AFTER_FAILURE_SECONDS)
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise _RefusedError("token_response_without_lifetime", _RETRY_AFTER_FAILURE_SECONDS)
    return _IssuedToken(
        access_token=access_token, expires_in=min(expires_in, _MAX_TOKEN_LIFETIME_SECONDS)
    )


def _refusal(response: httpx.Response) -> _RefusedError:
    ls_error = _decode_json_object(response.content).get("ls_error")
    reason = ls_error if isinstance(ls_error, str) and ls_error else str(response.status_code)
    status = HttpStatusError.from_response(
        response.status_code, response.headers.get("Retry-After")
    )
    return _RefusedError(reason, _retry_after(status))


def _retry_after(status: HttpStatusError) -> float:
    if not status.retryable:
        return _MAX_TOKEN_LIFETIME_SECONDS
    requested = status.retry_after_seconds or _RETRY_AFTER_FAILURE_SECONDS
    return min(max(requested, _RETRY_AFTER_FAILURE_SECONDS), _MAX_TOKEN_LIFETIME_SECONDS)


def _logged_refusal(error: Exception, audience: str) -> _RefusedError:
    attributes = {"agent_access_token.audience": audience}
    if isinstance(error, _RefusedError):
        logger.warning(
            "Agent access token refused: %s",
            logger.log_identifier(error.reason),
            attributes={**attributes, "agent_access_token.reason": error.reason},
        )
        return error
    logger.error("Agent access token mint failed unexpectedly", error=error, attributes=attributes)
    return _RefusedError(type(error).__name__, _RETRY_AFTER_FAILURE_SECONDS)
