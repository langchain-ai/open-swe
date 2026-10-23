"""The Open SWE backend this deployment calls back into, as the current run.

Dispatch signs a run token into each run's context. The backend reads the thread,
repository and run configuration from that token, so nothing the model writes can
point a call elsewhere. Every call, the loop's and the tools', is one endpoint of
the typed API both sides import (``runtime_api``), under ``/agent-api/``.

A task worker launched as an async subagent starts with a worker grant instead:
enough to read its coordinator's catalog, then ``adopt`` a run token of its own.
"""

import asyncio
import logging
import os
from collections.abc import Coroutine, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final
from weakref import WeakKeyDictionary

import httpx2
from langchain_core.messages import BaseMessage, ToolMessage, messages_to_dict
from langsmith.run_trees import RunTree
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from runtime_api.catalog import CATALOG, Catalog
from runtime_api.endpoint import Delivery, call
from runtime_api.loop import TOOL_RESULT, TOOL_STARTED, RunCall, ToolCallReport, ToolResultReport
from runtime_api.operations import RunRequest
from runtime_api.workers import ADOPT, Adopt

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


class OpenSweBackend:
    """The Open SWE backend, called with one run's token, or the grant a launched worker adopts it with."""

    def __init__(self, run_token: str | None, *, worker_grant: str | None = None) -> None:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        if not base:
            raise BackendCallError(f"{BACKEND_URL_ENV} is not configured")
        self._base = base
        self._run_token = run_token
        self._worker_grant = worker_grant if run_token is None else None
        # The worker's own catalog, once it adopted a run token: what it may offer the model.
        self.adopted: Catalog | None = None
        self.reports = ToolReports(self)

    @property
    def has_run(self) -> bool:
        return self._run_token is not None or self._worker_grant is not None

    @property
    def awaiting_adoption(self) -> bool:
        """A launched worker that has not yet traded its grant for a run token of its own."""
        return self._run_token is None and self._worker_grant is not None

    async def adopt(self, thread_id: str, run_id: str | None, instructions: str) -> None:
        """Become this worker thread's run: every later call, tools included, acts as it."""
        adopted = await call(
            self, ADOPT, Adopt(thread_id=thread_id, run_id=run_id, instructions=instructions)
        )
        self._run_token, self._worker_grant = adopted.run_token, None
        self.adopted = await self.catalog(run_id)

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
        bearer = self._run_token or self._worker_grant
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

    async def catalog(self, run_id: str | None) -> Catalog:
        return await call(self, CATALOG, RunRequest(run_id=run_id))

    async def tool_started(
        self, report: ToolCallReport, *, trace: Mapping[str, str] | None = None
    ) -> None:
        await call(self, TOOL_STARTED, report, headers=trace)

    async def tool_result(
        self,
        report: ToolCallReport,
        result: ToolMessage,
        *,
        trace: Mapping[str, str] | None = None,
    ) -> dict[str, JsonValue]:
        [message] = _json_dicts.validate_python(messages_to_dict([result]))
        finished = ToolResultReport.model_validate({**report.model_dump(), "result": message})
        return (await call(self, TOOL_RESULT, finished, headers=trace)).update


class ToolReports:
    """Tell the backend's transcript about the tool calls this runtime runs, off their path.

    Reports go out in the order they were made, one after another, while the calls
    carry on. A report the backend could not take is logged and dropped: the call
    it describes has run either way.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        self._last: asyncio.Task[None] | None = None

    def started(self, report: ToolCallReport) -> None:
        self._send(self._backend.tool_started(report, trace=TurnTrace.current_headers()))

    def finished(self, report: ToolCallReport, result: ToolMessage) -> None:
        # The call's run closes before the report is answered, and with it the way to
        # the turn's trace and the call's span, so both are found now.
        self._send(
            self._finished(report, result, TurnTrace.live_root(), TurnTrace.current_headers())
        )

    async def drain(self) -> None:
        """Wait for every report made so far, so the transcript has them before what follows."""
        if self._last is not None:
            await asyncio.shield(self._last)

    async def _finished(
        self,
        report: ToolCallReport,
        result: ToolMessage,
        turn: RunTree | None,
        trace: Mapping[str, str],
    ) -> None:
        # What the call changed in the thread's checkouts, for the turn's git metadata.
        update = await self._backend.tool_result(report, result, trace=trace)
        TurnTrace.record_evidence(update, turn)

    def _send(self, report: Coroutine[object, object, None]) -> None:
        self._last = asyncio.ensure_future(self._after(self._last, report))

    @staticmethod
    async def _after(
        previous: asyncio.Task[None] | None, report: Coroutine[object, object, None]
    ) -> None:
        if previous is not None:
            await previous
        try:
            await report
        except Exception:
            logger.warning("The backend did not record a tool call", exc_info=True)
