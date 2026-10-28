"""The Open SWE backend this deployment calls back into, as the current run.

MDA builds a thread's graph once for all its runs, so the graph holds no run of its
own: each call finds the run in progress (``current_run``) in the context LangGraph
hands it, and calls as that run.

Dispatch signs a run token into each run's context. The backend reads the thread,
repository and run configuration from that token, so nothing the model writes can
point a call elsewhere. Every call, the loop's and the tools', is one endpoint of
the typed API both sides import (``runtime_api``), under ``/agent-api/``.

A task worker launched as an async subagent starts with a worker grant instead:
enough to read its coordinator's catalog, then ``adopt`` a run token and catalog of
its own.
"""

import asyncio
import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final, Literal, cast
from weakref import WeakKeyDictionary

import httpx2
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    messages_to_dict,
)
from langgraph.runtime import get_runtime
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PrivateAttr,
    TypeAdapter,
    ValidationError,
)
from runtime_api.catalog import Catalog, OperationSpec
from runtime_api.endpoint import Delivery, call
from runtime_api.loop import RunCall
from runtime_api.workers import ADOPT, Adopt

from rt.outbox import Outbox
from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)

BACKEND_URL_ENV: Final = "OPEN_SWE_BACKEND_URL"
# Graph state the backend's hooks read that MDA's own middleware keeps.
_FORWARDED_STATE_KEYS: Final = ("run_model_call_count", "thread_model_call_count")
# What a call takes past its own ``timeout`` before it answers that it ran out: the
# sandbox's grace for an overrunning command, and killing it.
TOOL_CALL_SLACK_SECONDS: Final = 300.0
# Waits before sending again a call that got no usable answer, for as long as a
# backend rollout takes; which calls are sent again is each endpoint's ``delivery``.
_RETRY_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0, 30.0, 30.0, 30.0)
_GATEWAY_STATUSES: Final = frozenset({502, 503, 504})
_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])

# The ``task`` calls whose subagent is running right now; empty for the agent itself.
_namespace: ContextVar[tuple[str, ...]] = ContextVar("open_swe_namespace", default=())


# The tool call the agent's own tool-call chain is reporting right now, if any.
_reported_call: ContextVar[str | None] = ContextVar("open_swe_reported_call", default=None)


@contextmanager
def reporting(tool_call_id: str) -> Iterator[None]:
    """Name ``tool_call_id`` as the call being reported while the block runs it."""
    token = _reported_call.set(tool_call_id)
    try:
        yield
    finally:
        _reported_call.reset(token)


def reported_call() -> str | None:
    """The call the tool-call chain is reporting, or None outside any."""
    return _reported_call.get()


@contextmanager
def subagent_of(tool_call_id: str) -> Iterator[None]:
    """Name ``tool_call_id`` in the block's calls, as the ``task`` call its subagent runs in."""
    token = _namespace.set((*_namespace.get(), tool_call_id))
    try:
        yield
    finally:
        _namespace.reset(token)


class BackendCallError(RuntimeError):
    """The backend refused or could not answer a call; never carries the token."""


class BackendTimeoutError(BackendCallError):
    """The backend did not answer in time; what the call started may still be running."""


class CallFailure(BaseModel):
    """The exception the backend raised answering a call, as it reports it."""

    error: str
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class BackendUnavailableError(BackendCallError):
    """A transport or gateway failure left the call without a usable response."""


def held_state(state: Mapping[str, object]) -> dict[str, JsonValue]:
    """The graph state the backend's hooks keep, which goes back with every call."""
    held = state.get("open_swe_state")
    forwarded = {key: state[key] for key in _FORWARDED_STATE_KEYS if key in state}
    return _json_object.validate_python({**(held if isinstance(held, dict) else {}), **forwarded})


_GIT_TRACE_PREFIX: Final = "git_trace_"


def git_trace(state: Mapping[str, object]) -> dict[str, JsonValue]:
    """The turn's git trace the backend keeps in the run's state: its baseline and evidence."""
    return {
        key: value for key, value in held_state(state).items() if key.startswith(_GIT_TRACE_PREFIX)
    }


def namespace() -> list[str]:
    """The ``task`` calls the running subagent is nested in; empty for the agent itself."""
    return list(_namespace.get())


def human_messages(messages: Sequence[object]) -> list[dict[str, JsonValue]]:
    return _json_dicts.validate_python(
        messages_to_dict([message for message in messages if isinstance(message, HumanMessage)])
    )


def issuing_message_id(state: Mapping[str, object], tool_call_id: str) -> str | None:
    """The reply in the run's conversation that made the tool call ``tool_call_id``."""
    for message in reversed(_messages(state.get("messages"))):
        if isinstance(message, AIMessage) and any(
            call.get("id") == tool_call_id for call in message.tool_calls
        ):
            return message.id
    return None


def run_call[Call: RunCall](
    request: type[Call], run_id: str | None, state: Mapping[str, object], **extra: JsonValue
) -> Call:
    """A call of the loop: the run's identity, its conversation and held state, and the subagent calling."""
    return request.model_validate(
        {
            "run_id": run_id,
            "messages": _json_dicts.validate_python(
                messages_to_dict(_messages(state.get("messages")))
            ),
            "state": held_state(state),
            "namespace": list(_namespace.get()),
            **extra,
        }
    )


def _messages(value: object) -> list[BaseMessage]:
    if not isinstance(value, Sequence):
        return []
    return [message for message in value if isinstance(message, BaseMessage)]


class _Connections:
    """One client per event loop, so calls reuse their connections to the backend.

    The Agent Server runs graphs on more than one loop, and a client belongs to the
    loop it was opened on.
    """

    def __init__(self) -> None:
        self._clients: WeakKeyDictionary[asyncio.AbstractEventLoop, httpx2.AsyncClient] = (
            WeakKeyDictionary()
        )

    def client(self) -> httpx2.AsyncClient:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None or client.is_closed:
            client = httpx2.AsyncClient()
            self._clients[loop] = client
        return client


_CONNECTIONS: Final = _Connections()


def _call_failure(response: httpx2.Response) -> CallFailure | None:
    """The backend's own exception from an error response, if it reported one."""
    if response.status_code != 500:
        return None
    try:
        return CallFailure.model_validate_json(response.content)
    except ValidationError:
        return None


Graph = Literal["agent", "reviewer", "review-scout"]


class Run:
    """One run of a graph, as the backend knows it: what it calls with, what it was
    planned with, and what it has told the backend.

    MDA builds a graph once for every run of every thread, so nothing the graph holds
    may be a run's: its tools and middleware find the run in progress per call
    (``current_run``) and read what they need from it.
    """

    def __init__(self, context: RunContext) -> None:
        # Dispatch sends the catalog it planned the run with in every run's context.
        self._carried = context.catalog
        self._token = context.run_token
        self._worker_grant = context.worker_grant if context.run_token is None else None
        # The run's invocation, which its usage counts its replies by; a worker learns
        # its own when it adopts a run token.
        self.invocation_id = context.invocation_id
        self._planned: Catalog | None = None
        # The worker's own catalog, once it adopted a run token.
        self.adopted: Catalog | None = None
        self.backend = OpenSweBackend(self)
        # What the run tells the backend happened, until the backend takes it.
        self.outbox = Outbox(self.backend)
        self._locals: dict[object, object] = {}

    def local[T](self, key: object, make: Callable[[], T]) -> T:
        """Keep a lazily created value for this run alone."""
        if key not in self._locals:
            self._locals[key] = make()
        return cast(T, self._locals[key])

    @property
    def bearer(self) -> str | None:
        return self._token or self._worker_grant

    @property
    def has_run(self) -> bool:
        return self.bearer is not None

    @property
    def awaiting_adoption(self) -> bool:
        """A launched worker that has not yet traded its grant for a run token of its own."""
        return self._token is None and self._worker_grant is not None

    @property
    def planned(self) -> Catalog:
        """The catalog the run started with: its coordinator's, for a launched worker."""
        if self._planned is None:
            if self._carried is not None:
                self._planned = Catalog.model_validate(self._carried)
            elif self.has_run:
                raise BackendCallError("The run started without its catalog")
            else:
                # Runs without a context (state reads, assistant reads) never call a model or a tool.
                self._planned = Catalog()
        return self._planned

    @property
    def catalog(self) -> Catalog:
        """What the run may do: a launched worker's own catalog once it adopted its run."""
        return self.adopted if self.adopted is not None else self.planned

    async def adopt(self, thread_id: str, run_id: str | None, instructions: str) -> None:
        """Become this worker thread's run: every later call, tools included, acts as it."""
        adopted = await call(
            self.backend,
            ADOPT,
            Adopt(thread_id=thread_id, run_id=run_id, instructions=instructions),
        )
        self._token, self._worker_grant = adopted.run_token, None
        self.invocation_id = adopted.invocation_id
        self.adopted = adopted.catalog

    def grant(self, name: str) -> str | None:
        """The grant to call operation ``name`` with: an adopted worker's own, not its coordinator's."""
        own = next((op for op in self.catalog.operations if op.name == name), None)
        return None if own is None else own.grant


def current_run() -> Run:
    """The run in progress, which every call the graph makes acts as."""
    context = get_runtime().context
    if not isinstance(context, RunContext):
        raise BackendCallError(
            "This run carries no Open SWE run context; start it through Open SWE"
        )
    return context.run()


class RunLocal[T]:
    """A lazily created value owned by the run, never retained by the shared graph."""

    def __init__(self, make: Callable[[], T]) -> None:
        self._make = make

    def get(self, run: Run) -> T:
        return run.local(self, self._make)


class RunContext(BaseModel):
    """What Open SWE dispatch passes as the run context.

    LangGraph hands every node of a run the same instance, so the run it describes
    (``run``) is found there by every call the run makes, its subagents' included.
    """

    model_config = ConfigDict(populate_by_name=True)

    graph: Graph = "agent"
    # The Agent Server keeps only context keys this schema names, and reads the
    # protocol v2 stream marker from the context it mirrors into configurable.
    event_streaming_v2: bool = Field(default=False, alias="__event_streaming_v2")
    # Dunder keys stay out of the trace metadata the context is mirrored into.
    run_token: str | None = Field(default=None, alias="__run_token")
    # A launched task worker's first run: traded for its own run token before anything else.
    worker_grant: str | None = Field(default=None, alias="__worker_grant")
    invocation_id: str | None = None
    # JSON, not Catalog: the gRPC runtime serializes only the context's top level.
    catalog: dict[str, JsonValue] | None = Field(default=None, alias="__catalog")
    _run: Run | None = PrivateAttr(default=None)

    def run(self) -> Run:
        """The run this context starts."""
        if self._run is None:
            self._run = Run(self)
        return self._run


class OpenSweBackend:
    """The Open SWE backend, called as one run: ``run``, or else the run in progress."""

    def __init__(self, run: Run | None = None) -> None:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        if not base:
            raise BackendCallError(f"{BACKEND_URL_ENV} is not configured")
        self._base = base
        self._run = run

    @property
    def run(self) -> Run:
        return self._run if self._run is not None else current_run()

    @property
    def adopted(self) -> Catalog | None:
        return self.run.adopted

    @property
    def invocation_id(self) -> str | None:
        return self.run.invocation_id

    @property
    def outbox(self) -> Outbox:
        return self.run.outbox

    @property
    def awaiting_adoption(self) -> bool:
        return self.run.awaiting_adoption

    async def adopt(self, thread_id: str, run_id: str | None, instructions: str) -> None:
        await self.run.adopt(thread_id, run_id, instructions)

    async def post(
        self,
        path: str,
        body: dict[str, JsonValue],
        *,
        timeout: float,
        delivery: Delivery,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        """One call of the typed API, as this run: ``runtime_api``'s transport.

        ``headers`` is the trace position to nest the call's spans under, by default
        the running call's span.
        """
        return await self._post(path, body, seconds=timeout, delivery=delivery, trace=headers)

    async def _post(
        self,
        path: str,
        body: Mapping[str, JsonValue],
        *,
        seconds: float,
        delivery: Delivery,
        trace: Mapping[str, str] | None = None,
    ) -> JsonValue:
        """POST as this run, its spans nested under ``trace``, by default the running call's span."""
        label = path.rsplit("/", 1)[-1]
        bearer = self.run.bearer
        if bearer is None:
            raise BackendCallError(
                "This run carries no Open SWE run token; start it through Open SWE"
            )
        headers = {
            **(TurnTrace.current_headers() if trace is None else trace),
            "Authorization": f"Bearer {bearer}",
        }
        waits = iter(_RETRY_SECONDS)
        while True:
            try:
                response = await _CONNECTIONS.client().post(
                    f"{self._base}{path}",
                    headers=headers,
                    json=dict(body),
                    timeout=seconds,
                )
                response.raise_for_status()
                break
            except httpx2.TimeoutException as exc:
                raise BackendTimeoutError(f"{label} failed ({type(exc).__name__})") from exc
            except httpx2.HTTPError as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx2.HTTPStatusError) else None
                )
                if isinstance(exc, httpx2.HTTPStatusError) and (
                    failure := _call_failure(exc.response)
                ):
                    # What the backend itself raised, so the run fails with the cause, as in-process.
                    raise BackendCallError(f"{label} failed: {failure.describe()}") from exc
                unreached = (
                    isinstance(exc, httpx2.ConnectError)
                    or (status == 503 and delivery != "once")
                    or (status == 502 and delivery == "idempotent")
                )
                if unreached and (wait := next(waits, None)) is not None:
                    logger.warning(
                        "Backend unavailable; retrying",
                        extra={"endpoint": label, "status_code": status, "wait_seconds": wait},
                    )
                    await asyncio.sleep(wait)
                    continue
                error = (
                    BackendUnavailableError
                    if isinstance(exc, httpx2.TransportError) or status in _GATEWAY_STATUSES
                    else BackendCallError
                )
                raise error(f"{label} failed ({type(exc).__name__})") from exc
        return _json.validate_python(response.json())

    def grant(self, spec: OperationSpec) -> str | None:
        return self.run.grant(spec.name)
