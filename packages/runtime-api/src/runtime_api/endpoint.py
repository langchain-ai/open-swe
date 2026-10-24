"""A call MDA makes on the backend, typed at both ends.

Each ``Endpoint`` names one call, the request and response models it carries, how
long it may take and when it may be sent again. The runtime calls it through
``call``; the backend serves it through ``Handlers``. Both import the same
definition, so a change to a call is a type error on the side that has not caught up.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from pydantic import BaseModel, JsonValue, ValidationError

API_PREFIX: Final = "/agent-api/"
# The route the backend serves every endpoint under.
API_PATH: Final = API_PREFIX + "{endpoint}"
# Preparation waits on the review scout's walkthrough for up to ten minutes; a tool
# call can run a long command.
LONG_CALL_SECONDS: Final = 1800.0
# A call of the model loop, which only reads and writes the run's own records.
LOOP_CALL_SECONDS: Final = 300.0

# When a call that got no usable answer is sent again, for as long as a backend
# rollout takes. A refused connection never reached the backend, so every call is
# sent again after one ("once"). A gateway 503 usually means no backend was ready
# to take it ("unprocessed"); a 502 can also follow a backend that took the call
# and died, so only a call that is safe to repeat is sent again after one
# ("idempotent"). Nothing is sent again after a 504: the backend may still be running it.
type Delivery = Literal["once", "unprocessed", "idempotent"]


@dataclass(frozen=True)
class Endpoint[Request: BaseModel, Response: BaseModel]:
    name: str
    request: type[Request]
    response: type[Response]
    seconds: float = LONG_CALL_SECONDS
    delivery: Delivery = "once"

    @property
    def path(self) -> str:
        return API_PREFIX + self.name


class Transport(Protocol):
    """How the runtime reaches the backend: one JSON POST, authenticated as the run.

    ``headers`` carries what the call says beyond its body, such as the trace
    position the backend nests its spans under.
    """

    async def post(
        self,
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue: ...


async def call[Request: BaseModel, Response: BaseModel](
    transport: Transport,
    endpoint: Endpoint[Request, Response],
    request: Request,
    *,
    timeout: float | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    raw = await transport.post(
        endpoint.path,
        request.model_dump(mode="json"),
        timeout=endpoint.seconds if timeout is None else timeout,
        delivery=endpoint.delivery,
        headers=headers,
    )
    return endpoint.response.model_validate(raw)


class UnknownEndpointError(LookupError):
    """A call named an endpoint this backend does not serve."""


class InvalidRequestError(ValueError):
    """A call's body is not its endpoint's request; what its handler raises never is."""


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
            try:
                request = endpoint.request.model_validate(raw)
            except ValidationError as exc:
                raise InvalidRequestError(f"{endpoint.name}: {exc}") from exc
            response = await handler(context, request)
            return response.model_dump(mode="json")

        self._served[endpoint.name] = dispatch

    def serves(self, name: str) -> bool:
        return name in self._served

    async def dispatch(self, name: str, context: Context, raw: JsonValue) -> JsonValue:
        served = self._served.get(name)
        if served is None:
            raise UnknownEndpointError(name)
        return await served(context, raw)
