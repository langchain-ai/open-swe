"""A call MDA makes on the backend, typed at both ends.

Each ``Endpoint`` names one call and the request and response models it carries.
The runtime calls it through ``call``; the backend serves it through ``Handlers``.
Both import the same definition, so a change to a call is a type error on the
side that has not caught up.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Protocol

from pydantic import BaseModel, JsonValue

API_PREFIX: Final = "/remote-runtime/api/"
# The route the backend serves every endpoint under.
API_PATH: Final = API_PREFIX + "{endpoint}"


@dataclass(frozen=True)
class Endpoint[Request: BaseModel, Response: BaseModel]:
    name: str
    request: type[Request]
    response: type[Response]

    @property
    def path(self) -> str:
        return API_PREFIX + self.name


class Transport(Protocol):
    """How the runtime reaches the backend: one JSON POST, authenticated as the run."""

    async def post(
        self, path: str, body: dict[str, JsonValue], *, timeout: float | None = None
    ) -> JsonValue: ...


async def call[Request: BaseModel, Response: BaseModel](
    transport: Transport,
    endpoint: Endpoint[Request, Response],
    request: Request,
    *,
    timeout: float | None = None,
) -> Response:
    raw = await transport.post(endpoint.path, request.model_dump(mode="json"), timeout=timeout)
    return endpoint.response.model_validate(raw)


class UnknownEndpointError(LookupError):
    """A call named an endpoint this backend does not serve."""


type Handler[Context, Request: BaseModel, Response: BaseModel] = Callable[
    [Context, Request], Awaitable[Response]
]


class Handlers[Context]:
    """The backend's handler for each endpoint, each typed by the endpoint it serves."""

    def __init__(self) -> None:
        self._served: dict[str, Callable[[Context, JsonValue], Awaitable[JsonValue]]] = {}

    def serve[Request: BaseModel, Response: BaseModel](
        self,
        endpoint: Endpoint[Request, Response],
        handler: Handler[Context, Request, Response],
    ) -> None:
        async def dispatch(context: Context, raw: JsonValue) -> JsonValue:
            response = await handler(context, endpoint.request.model_validate(raw))
            return response.model_dump(mode="json")

        self._served[endpoint.name] = dispatch

    async def dispatch(self, name: str, context: Context, raw: JsonValue) -> JsonValue:
        served = self._served.get(name)
        if served is None:
            raise UnknownEndpointError(name)
        return await served(context, raw)
