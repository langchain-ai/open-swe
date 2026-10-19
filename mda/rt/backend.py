"""The Open SWE backend this deployment calls back into, as the current run.

Dispatch signs a run token into each run's context. The backend reads the thread,
repository and run configuration from that token, so nothing the model writes can
point a call elsewhere. Every call is ``POST /remote-runtime/hooks/{hook}``.
"""

import asyncio
import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final, Literal
from weakref import WeakKeyDictionary

import httpx2
from langchain_core.messages import BaseMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from rt.models import ModelSpec, RunModels

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
]
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
    # Tools the graph keeps from the model, including MDA's own of the same name.
    excluded: list[str] = []
    # Tools the model calls from code mode rather than directly.
    code_only: list[str] = []
    # Tools the model calls both directly and from code mode.
    also_from_code: list[str] = []
    # Tools the model labels each call of, with what the label argument tells it.
    described: dict[str, str] = {}


class PhaseReply(BaseModel):
    update: dict[str, JsonValue] = {}
    messages: list[dict[str, JsonValue]] = []
    jump_to: str | None = None
    events: list[JsonValue] = []
    # Set by before_model: what to send the model this time.
    system_prompt: str | None = None
    hidden_tools: list[str] = []
    model: ModelSpec | None = None
    fallback: ModelSpec | None = None
    use_gateway: bool = False


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
    """The Open SWE backend, called with one run's token."""

    def __init__(self, run_token: str | None) -> None:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        if not base:
            raise BackendCallError(f"{BACKEND_URL_ENV} is not configured")
        self._base = base
        self._run_token = run_token

    @property
    def has_run(self) -> bool:
        return self._run_token is not None

    async def call(
        self,
        hook: Hook,
        body: Mapping[str, JsonValue] | None = None,
        *,
        seconds: float | None = None,
    ) -> dict[str, JsonValue]:
        if self._run_token is None:
            raise BackendCallError(
                "This run carries no Open SWE run token; start it through Open SWE"
            )
        if seconds is None:
            seconds = _LONG_CALL_SECONDS if hook == "prepare" else _HOOK_SECONDS
        for attempt in range(3):
            try:
                response = await _CONNECTIONS.client().post(
                    f"{self._base}/remote-runtime/hooks/{hook}",
                    headers={"Authorization": f"Bearer {self._run_token}"},
                    json=dict(body or {}),
                    timeout=seconds,
                )
                response.raise_for_status()
                break
            except httpx2.TimeoutException as exc:
                raise BackendTimeoutError(f"{hook} failed ({type(exc).__name__})") from exc
            except httpx2.ConnectError as exc:
                if attempt >= 2:
                    raise BackendUnavailableError(f"{hook} failed (ConnectError)") from exc
                logger.warning("Backend connection failed; retrying", extra={"hook": hook})
                await asyncio.sleep((1, 3)[attempt])
            except httpx2.HTTPError as exc:
                gateway_failure = isinstance(
                    exc, httpx2.HTTPStatusError
                ) and exc.response.status_code in {502, 503, 504}
                if isinstance(exc, httpx2.HTTPStatusError) and (
                    failure := _hook_failure(exc.response)
                ):
                    # What the hook itself raised, so the run fails with the cause, as in-process.
                    raise BackendCallError(f"{hook} failed: {failure.describe()}") from exc
                error = (
                    BackendUnavailableError
                    if isinstance(exc, httpx2.TransportError) or gateway_failure
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

    async def tool_result(self, body: Mapping[str, JsonValue], result: ToolMessage) -> None:
        [message] = _json_list.validate_python(messages_to_dict([result]))
        await self.call("tool_result", {**body, "result": message})
