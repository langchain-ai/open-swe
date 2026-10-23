"""The Open SWE backend this deployment calls back into, as the current run.

Dispatch signs a run token into each run's context. The backend reads the thread,
repository and run configuration from that token, so nothing the model writes can
point a call elsewhere. Every call is ``POST /remote-runtime/hooks/{hook}``.

A task worker launched as an async subagent starts with a worker grant instead:
enough to read its coordinator's catalog, then ``adopt`` a run token of its own.
"""

import asyncio
import logging
import os
from collections.abc import Coroutine, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final, Literal
from weakref import WeakKeyDictionary

import httpx2
from langchain_core.messages import BaseMessage, ToolMessage, messages_to_dict
from langsmith.run_trees import RunTree
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from rt.models import ModelSpec, RunModels
from rt.sandbox import SandboxSpec
from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)

BACKEND_URL_ENV: Final = "OPEN_SWE_BACKEND_URL"
# Graph state the backend's hooks read that MDA's own middleware keeps.
_FORWARDED_STATE_KEYS: Final = ("run_model_call_count", "thread_model_call_count")
# Preparation waits on the review scout's walkthrough for up to ten minutes; a tool
# call can run a long command.
_LONG_CALL_SECONDS: Final = 1800.0
_HOOK_SECONDS: Final = 300.0
# What a tool call takes past its own ``timeout`` before it answers that it ran out:
# the sandbox's grace for an overrunning command, killing it, and the tool middleware.
_TOOL_CALL_SLACK_SECONDS: Final = 300.0

Hook = Literal[
    "catalog",
    "prepare",
    "before_model",
    "model_result",
    "after_model",
    "after_agent",
    "call",
    "tool_started",
    "tool_result",
    # The thread's box was replaced: the backend connects to the fresh one.
    "sandbox",
    # A tool this runtime ran in the box found it gone: the backend tells the person.
    "sandbox_unreachable",
    # The LangSmith browser MDA gave the thread's agent, for the dashboard to show.
    "browser",
    "launch_worker",
    "worker_launched",
    "adopt",
]
# Waits before resending a call, for as long as a backend rollout takes. A refused
# connection never reached the backend, so every hook is resent after one. A gateway
# 502 or 503 usually means no backend was ready, but can also follow a backend that
# took the call and died, so only calls that are safe to repeat are resent after
# one: ``catalog`` only reads, ``prepare`` a restarted run makes again anyway, and
# ``launch_worker`` only authorizes a launch or returns the worker it already made.
# ``before_model`` only builds the next model call, so it is also resent after a 503,
# the answer a rollout gives it. A tool call is never resent: it may have run. Nor is
# anything after a 504: the backend may still be running the call.
_RETRY_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0, 30.0, 30.0, 30.0)
_REPEATABLE_HOOKS: Final[frozenset[Hook]] = frozenset({"catalog", "prepare", "launch_worker"})
_RESENT_AFTER_503: Final[frozenset[Hook]] = frozenset({*_REPEATABLE_HOOKS, "before_model"})
_GATEWAY_STATUSES: Final = frozenset({502, 503, 504})
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])

# The ``task`` calls whose subagent is running right now; empty for the agent itself.
_namespace: ContextVar[tuple[str, ...]] = ContextVar("open_swe_namespace", default=())


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


class HookFailure(BaseModel):
    """The exception a backend hook raised, as the backend reports it."""

    error: str
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class BackendUnavailableError(BackendCallError):
    """A transport or gateway failure left the call without a usable response."""


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class SubagentSpec(BaseModel):
    name: str
    description: str
    system_prompt: str
    # None: every tool the parent has.
    tools: list[str] | None = None


class Catalog(BaseModel):
    """The tools, subagents and models of the graph this run plays."""

    tools: list[ToolSpec] = []
    subagents: list[SubagentSpec] = []
    models: RunModels | None = None
    trace_metadata: dict[str, JsonValue] = {}
    # Tools the graph keeps from the model, including MDA's own of the same name.
    excluded: list[str] = []
    # Tools the model calls from code mode rather than directly.
    code_only: list[str] = []
    # Tools the model calls both directly and from code mode.
    also_from_code: list[str] = []
    # Tools the model labels each call of, with what the label argument tells it.
    described: dict[str, str] = {}
    # The thread's box; None runs every file and shell tool on the backend alone.
    sandbox: SandboxSpec | None = None
    # Where the Agent Server reports a launched worker's run ending; None launches none.
    worker_webhook: str | None = None


class AdoptReply(BaseModel):
    run_token: str


class PhaseReply(BaseModel):
    update: dict[str, JsonValue] = {}
    messages: list[dict[str, JsonValue]] = []
    jump_to: str | None = None
    events: list[JsonValue] = []
    # Set by before_model: what to send the model this time.
    system_prompt: str | None = None
    hidden_tools: list[str] = []
    model: ModelSpec | None = None
    vision: ModelSpec | None = None
    fallback: ModelSpec | None = None
    use_gateway: bool = False
    trace_metadata: dict[str, JsonValue] = {}


class ToolReply(BaseModel):
    message: dict[str, JsonValue]
    messages: list[dict[str, JsonValue]] = []
    update: dict[str, JsonValue] = {}
    events: list[JsonValue] = []


def run_body(
    run_id: str | None, state: Mapping[str, object], **extra: JsonValue
) -> dict[str, JsonValue]:
    """The run's identity, the graph state the backend's hooks read, and the subagent calling."""
    messages = state.get("messages")
    held = state.get("open_swe_state")
    forwarded = {key: state[key] for key in _FORWARDED_STATE_KEYS if key in state}
    return _json_object.validate_python(
        {
            "run_id": run_id,
            "messages": messages_to_dict(_messages(messages)),
            "state": {**(held if isinstance(held, dict) else {}), **forwarded},
            "namespace": list(_namespace.get()),
            **extra,
        }
    )


def _messages(value: object) -> list[BaseMessage]:
    if not isinstance(value, Sequence):
        return []
    return [message for message in value if isinstance(message, BaseMessage)]


class _Connections:
    """One client per event loop, so hook calls reuse their connections to the backend.

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


def _hook_failure(response: httpx2.Response) -> HookFailure | None:
    """The hook's own exception from a backend error response, if it reported one."""
    if response.status_code != 500:
        return None
    try:
        return HookFailure.model_validate_json(response.content)
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
        body: dict[str, JsonValue] = {
            "thread_id": thread_id,
            "run_id": run_id,
            "instructions": instructions,
        }
        reply = AdoptReply.model_validate(await self.call("adopt", body))
        self._run_token, self._worker_grant = reply.run_token, None
        self.adopted = await self.catalog(run_id)

    async def call(
        self,
        hook: Hook,
        body: Mapping[str, JsonValue] | None = None,
        *,
        seconds: float | None = None,
    ) -> dict[str, JsonValue]:
        bearer = self._run_token or self._worker_grant
        if bearer is None:
            raise BackendCallError(
                "This run carries no Open SWE run token; start it through Open SWE"
            )
        if seconds is None:
            seconds = _LONG_CALL_SECONDS if hook == "prepare" else _HOOK_SECONDS
        waits = iter(_RETRY_SECONDS)
        while True:
            try:
                response = await _CONNECTIONS.client().post(
                    f"{self._base}/remote-runtime/hooks/{hook}",
                    headers={"Authorization": f"Bearer {bearer}"},
                    json=dict(body or {}),
                    timeout=seconds,
                )
                response.raise_for_status()
                break
            except httpx2.TimeoutException as exc:
                raise BackendTimeoutError(f"{hook} failed ({type(exc).__name__})") from exc
            except httpx2.HTTPError as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx2.HTTPStatusError) else None
                )
                if isinstance(exc, httpx2.HTTPStatusError) and (
                    failure := _hook_failure(exc.response)
                ):
                    # What the hook itself raised, so the run fails with the cause, as in-process.
                    raise BackendCallError(f"{hook} failed: {failure.describe()}") from exc
                unreached = (
                    isinstance(exc, httpx2.ConnectError)
                    or (status == 503 and hook in _RESENT_AFTER_503)
                    or (status == 502 and hook in _REPEATABLE_HOOKS)
                )
                if unreached and (wait := next(waits, None)) is not None:
                    logger.warning(
                        "Backend unavailable; retrying",
                        extra={"hook": hook, "status_code": status, "wait_seconds": wait},
                    )
                    await asyncio.sleep(wait)
                    continue
                error = (
                    BackendUnavailableError
                    if isinstance(exc, httpx2.TransportError) or status in _GATEWAY_STATUSES
                    else BackendCallError
                )
                raise error(f"{hook} failed ({type(exc).__name__})") from exc
        return _json_object.validate_python(response.json())

    async def catalog(self, run_id: str | None) -> Catalog:
        return Catalog.model_validate(await self.call("catalog", {"run_id": run_id}))

    async def phase(self, hook: Hook, body: Mapping[str, JsonValue]) -> PhaseReply:
        return PhaseReply.model_validate(await self.call(hook, body))

    async def tool(self, body: Mapping[str, JsonValue]) -> ToolReply:
        arguments = body.get("arguments")
        requested = arguments.get("timeout") if isinstance(arguments, dict) else None
        # A call with its own timeout, such as a long ``execute``, must be able to run it
        # out and answer that it did, as it does in-process, rather than outlive this one.
        seconds = _LONG_CALL_SECONDS
        if isinstance(requested, int | float) and not isinstance(requested, bool):
            seconds = max(seconds, requested + _TOOL_CALL_SLACK_SECONDS)
        return ToolReply.model_validate(await self.call("call", body, seconds=seconds))

    async def model_result(
        self, body: Mapping[str, JsonValue], response: Sequence[BaseMessage]
    ) -> list[dict[str, JsonValue]]:
        reply = await self.call(
            "model_result",
            {**body, "response": _json_list.validate_python(messages_to_dict(list(response)))},
        )
        observed = reply.get("response")
        return [m for m in observed if isinstance(m, dict)] if isinstance(observed, list) else []

    async def tool_started(self, body: Mapping[str, JsonValue]) -> None:
        await self.call("tool_started", body)

    async def tool_result(
        self, body: Mapping[str, JsonValue], result: ToolMessage
    ) -> dict[str, JsonValue]:
        [message] = _json_list.validate_python(messages_to_dict([result]))
        reply = await self.call("tool_result", {**body, "result": message})
        update = reply.get("update")
        return _json_object.validate_python(update) if isinstance(update, dict) else {}


class ToolReports:
    """Tell the backend's transcript about the tool calls this runtime runs, off their path.

    Reports go out in the order they were made, one after another, while the calls
    carry on. A report the backend could not take is logged and dropped: the call
    it describes has run either way.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        self._last: asyncio.Task[None] | None = None

    def started(self, body: Mapping[str, JsonValue]) -> None:
        self._send(self._backend.tool_started(body))

    def finished(self, body: Mapping[str, JsonValue], result: ToolMessage) -> None:
        # The call's run closes before the report is answered, and with it the way to
        # the turn's trace, so the trace is found now.
        self._send(self._finished(body, result, TurnTrace.live_root()))

    async def drain(self) -> None:
        """Wait for every report made so far, so the transcript has them before what follows."""
        if self._last is not None:
            await asyncio.shield(self._last)

    async def _finished(
        self, body: Mapping[str, JsonValue], result: ToolMessage, turn: RunTree | None
    ) -> None:
        # What the call changed in the thread's checkouts, for the turn's git metadata.
        TurnTrace.record_evidence(await self._backend.tool_result(body, result), turn)

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
