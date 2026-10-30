"""What this runtime tells the backend happened, held until the backend says it took it.

Nothing the run does next waits on a report. Each reply and tool call, each inbox
receipt and each notice (work the backend does for the run whose answer the run
never reads) joins the outbox in the order it was made, and goes out with whatever
else is waiting in one ``reports`` call, after a moment's gathering unless a phase
waits on it. An item
leaves the outbox only once an answer names it taken; until then it is sent again,
for as long as the backend takes to come back. Its key, unique in the thread, has
the backend record it once however often it arrives.

The items still waiting ride in the run's graph state (``open_swe_outbox``), written
with the step that made them: alongside the reply, the tool call's result, or the
hook's update. A step that reached the run's checkpoint takes what it reported with
it, so a restarted process, or the thread's next run if this one failed, sends it
then; a step that did not runs again and reports again, under the same key where
it is the same call.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Final, Literal

from langchain_core.messages import ToolMessage, messages_to_dict
from langsmith.run_trees import RunTree
from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.endpoint import Endpoint, Transport, call
from runtime_api.loop import (
    INBOX_RECEIVED,
    MODEL_RESULT,
    TOOL_RESULT,
    TOOL_STARTED,
    InboxItems,
    InboxReceipt,
    ModelResult,
    ToolCallReport,
    ToolResultReply,
    ToolResultReport,
)
from runtime_api.operations import RunRequest
from runtime_api.reports import (
    REPORTS,
    Item,
    Received,
    Reports,
    ReportsTaken,
    Sent,
    ToolFinished,
    item,
)

from rt.tracing import TurnTrace

logger = logging.getLogger(__name__)

# The graph state key the items still waiting ride in.
OUTBOX_KEY: Final = "open_swe_outbox"
_BATCH_ITEMS: Final = 64
# How long a batch waits for more before it goes, so a reply and the tool call it makes
# travel together.
_GATHER_SECONDS: Final = 0.05
_BATCH_BYTES: Final = 4 * 1024 * 1024
# Waits before sending again what the backend did not take, capped at the last.
_RETRY_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
# How long sending goes on without the backend taking anything; what is left waits in
# the run's state for whatever runs next on the thread.
_GIVE_UP_SECONDS: Final = 3600.0
# How long a phase waits for the reports before it: as long as a call of the loop
# waits out a backend rollout.
DRAIN_SECONDS: Final = 300.0
# An item the backend refused this often is dropped, so one it can never record does
# not hold up the rest.
_MAX_REFUSALS: Final = 3

_json_dicts = TypeAdapter(list[dict[str, JsonValue]])


class _ToolResultRef(Sent):
    endpoint: Literal["tool_result_ref"] = "tool_result_ref"
    request: ToolCallReport
    result_id: str | None = None

    def restore(self, messages: Mapping[str, ToolMessage]) -> ToolFinished:
        message = messages.get(self.request.tool_call_id)
        if message is None:
            raise ValueError(f"Missing checkpoint tool result: {self.request.tool_call_id}")
        [result] = _json_dicts.validate_python(
            messages_to_dict([message.model_copy(update={"id": self.result_id})])
        )
        return ToolFinished(
            endpoint="tool_result",
            key=self.key,
            trace=self.trace,
            request=ToolResultReport.model_validate(
                {**self.request.model_dump(), "result": result}
            ),
        )


class _Carried(BaseModel):
    """An item as the run's state carries it."""

    order: int
    item: Item | _ToolResultRef


@dataclass
class _Waiting:
    item: Item
    order: int
    size: int
    done: asyncio.Future[None]
    # The turn a tool result's git evidence goes to; unknown for one carried over.
    turn: RunTree | None = None
    refusals: int = 0

    def resolve(self) -> None:
        if not self.done.done():
            self.done.set_result(None)


@dataclass
class _Backoff:
    waits: Iterator[float] = field(default_factory=lambda: iter(_RETRY_SECONDS))
    since: float | None = None

    def next(self) -> float:
        if self.since is None:
            self.since = time.monotonic()
        return next(self.waits, _RETRY_SECONDS[-1])

    @property
    def exhausted(self) -> bool:
        return self.since is not None and time.monotonic() - self.since > _GIVE_UP_SECONDS


def _tool_messages(state: Mapping[str, object]) -> dict[str, ToolMessage]:
    messages = state.get("messages")
    return (
        {message.tool_call_id: message for message in messages if isinstance(message, ToolMessage)}
        if isinstance(messages, Sequence)
        else {}
    )


def _held(state: Mapping[str, object]) -> Mapping[str, object]:
    held = state.get(OUTBOX_KEY)
    return held if isinstance(held, Mapping) else {}


def carry(
    left: dict[str, JsonValue] | None, right: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    """The outbox's state reducer: what an update holds replaces, and a none lets go."""
    merged = {**(left or {}), **(right or {})}
    return {key: value for key, value in merged.items() if value is not None}


def _key(request: RunRequest, *parts: str) -> str:
    return ":".join((request.run_id or uuid.uuid4().hex, *parts))


class Outbox:
    """The run's reports and notices, until the backend takes them."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._waiting: dict[str, _Waiting] = {}
        # Every key this process holds or held, and those no longer waiting.
        self._known: set[str] = set()
        self._settled: set[str] = set()
        self._last = 0
        self._sending: asyncio.Task[None] | None = None
        # Set while a phase waits on the outbox, so what waits goes out at once.
        self._hurry = asyncio.Event()
        self._waiters = 0

    def replied(self, result: ModelResult) -> None:
        self._add(MODEL_RESULT, result, uuid.uuid4().hex)

    def started(self, report: ToolCallReport) -> None:
        self._add(TOOL_STARTED, report, _key(report, report.tool_call_id, "started"))

    def finished(self, report: ToolCallReport, result: ToolMessage) -> None:
        [message] = _json_dicts.validate_python(messages_to_dict([result]))
        finished = ToolResultReport.model_validate({**report.model_dump(), "result": message})
        # The call's run closes before the backend answers, and with it the way to the
        # turn's trace, so the turn its git evidence goes to is found now.
        self._add(
            TOOL_RESULT,
            finished,
            _key(report, report.tool_call_id, "finished"),
            turn=TurnTrace.live_root(),
        )

    def received(self, receipt: InboxReceipt) -> None:
        self._add(INBOX_RECEIVED, receipt, uuid.uuid4().hex)

    def notify[Request: RunRequest, Response: BaseModel](
        self, endpoint: Endpoint[Request, Response], request: Request
    ) -> None:
        """Have the backend do ``request``'s work for the run, without waiting for it."""
        # Work done once a run is keyed by it, so a hook that runs again asks once.
        key = (
            uuid.uuid4().hex if endpoint.delivery == "idempotent" else _key(request, endpoint.name)
        )
        self._add(endpoint, request, key)

    async def drain(self) -> None:
        """Wait until the backend took every item made so far, or it is too long."""
        await self._until(list(self._waiting.values()))

    def receiving(self) -> InboxItems:
        """What the inbox receipts the backend has yet to take hold."""
        receipts = [
            waiting.item.request
            for waiting in self._waiting.values()
            if isinstance(waiting.item, Received)
        ]
        return InboxItems(
            queued=[seq for receipt in receipts for seq in receipt.items.queued],
            events=[event for receipt in receipts for event in receipt.items.events],
        )

    def adopt(self, state: Mapping[str, object]) -> None:
        """Take over what ``state`` carries that this process never held: a restarted
        run's, or an earlier run's on the thread, which goes out first."""
        messages = _tool_messages(state)
        for key, value in _held(state).items():
            if key in self._known:
                continue
            try:
                carried = _Carried.model_validate(value)
                restored = (
                    carried.item.restore(messages)
                    if isinstance(carried.item, _ToolResultRef)
                    else carried.item
                )
            except ValueError:
                # Unreadable, or a tool result the checkpoint no longer holds, such as one
                # summarized away: failing would fail every later step of the thread.
                logger.warning("Dropping an outbox item the run's state could not rebuild")
                self._known.add(key)
                self._settled.add(key)
                continue
            self._hold(restored, carried.order)

    def carried(self, state: Mapping[str, object]) -> dict[str, JsonValue] | None:
        """What the run's state carries of the outbox from here: what still waits, and
        what no longer does as none. None when that changes nothing."""
        self.adopt(state)
        held = _held(state)
        update: dict[str, JsonValue] = {
            key: None for key in held if key in self._settled and key not in self._waiting
        }
        messages = _tool_messages(state)
        for key, waiting in self._waiting.items():
            saved: Item | _ToolResultRef = waiting.item
            if isinstance(saved, ToolFinished) and not saved.request.namespace:
                reference = _ToolResultRef(
                    key=saved.key,
                    trace=saved.trace,
                    request=ToolCallReport.model_validate(saved.request.model_dump()),
                    result_id=ToolMessage.model_validate(saved.request.result["data"]).id,
                )
                if saved.request.tool_call_id in messages and reference.restore(messages) == saved:
                    saved = reference
            carried = _Carried(order=waiting.order, item=saved).model_dump(mode="json")
            if carried != held.get(key):
                update[key] = carried
        return update or None

    def _add[Request: BaseModel, Response: BaseModel](
        self,
        endpoint: Endpoint[Request, Response],
        request: Request,
        key: str,
        *,
        turn: RunTree | None = None,
    ) -> _Waiting:
        made = item(endpoint, request, key=key, trace=TurnTrace.current_headers())
        self._last = max(time.time_ns(), self._last + 1)
        return self._hold(made, self._last, turn=turn)

    def _hold(self, held: Item, order: int, *, turn: RunTree | None = None) -> _Waiting:
        self._known.add(held.key)
        waiting = self._waiting.get(held.key)
        if waiting is None:
            waiting = _Waiting(
                item=held,
                order=order,
                size=len(held.model_dump_json()),
                done=asyncio.get_running_loop().create_future(),
                turn=turn,
            )
            self._waiting[held.key] = waiting
            self._last = max(self._last, order)
        if self._sending is None or self._sending.done():
            self._sending = asyncio.ensure_future(self._send())
        return waiting

    async def _until(self, waiting: list[_Waiting]) -> None:
        pending = [w.done for w in waiting if not w.done.done()]
        if not pending:
            return
        self._waiters += 1
        self._hurry.set()
        try:
            _, late = await asyncio.wait(pending, timeout=DRAIN_SECONDS)
        finally:
            self._waiters -= 1
            if not self._waiters:
                self._hurry.clear()
        if late:
            logger.warning(
                "Going on before the backend took the run's reports",
                extra={"reports_waiting": len(late), "waited_seconds": DRAIN_SECONDS},
            )

    def _batch(self) -> list[_Waiting]:
        batch: list[_Waiting] = []
        size = 0
        for waiting in sorted(self._waiting.values(), key=lambda w: w.order):
            if batch and (len(batch) == _BATCH_ITEMS or size + waiting.size > _BATCH_BYTES):
                break
            batch.append(waiting)
            size += waiting.size
        return batch

    async def _send(self) -> None:
        backoff = _Backoff()
        while self._waiting:
            if not self._hurry.is_set():
                # What the run makes next joins this batch, unless a phase waits on it.
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._hurry.wait(), _GATHER_SECONDS)
            batch = self._batch()
            try:
                answer = await call(
                    self._transport,
                    REPORTS,
                    Reports(items=[waiting.item for waiting in batch]),
                    # Each item names the span it nests under; the batch none.
                    headers={},
                )
            except Exception:
                if backoff.exhausted:
                    logger.exception(
                        "The backend took none of the run's reports; leaving them in its state",
                        extra={"reports_waiting": len(self._waiting)},
                    )
                    return
                wait = backoff.next()
                logger.warning(
                    "The backend did not take the run's reports; sending them again",
                    extra={"reports": len(batch), "wait_seconds": wait},
                    exc_info=True,
                )
                await asyncio.sleep(wait)
                continue
            if self._settle(batch, answer):
                backoff = _Backoff()
            else:
                # Another delivery of the first is still recording it.
                await asyncio.sleep(backoff.next())

    def _settle(self, batch: list[_Waiting], answer: ReportsTaken) -> bool:
        """Let go of what ``answer`` took; whether anything moved."""
        for waiting, reply in zip(batch, answer.replies, strict=False):
            if waiting.item.endpoint == TOOL_RESULT.name and waiting.turn is not None:
                # What the call changed in the thread's checkouts, for the turn's git metadata.
                update = ToolResultReply.model_validate(reply).update
                TurnTrace.record_evidence(update, waiting.turn)
            self._let_go(waiting)
        moved = bool(answer.replies)
        if answer.refused is not None and len(answer.replies) < len(batch):
            waiting = batch[len(answer.replies)]
            waiting.refusals += 1
            logger.warning(
                "The backend could not record a report",
                extra={
                    "endpoint": waiting.item.endpoint,
                    "refusals": waiting.refusals,
                    "refusal": answer.refused,
                },
            )
            if waiting.refusals >= _MAX_REFUSALS:
                self._let_go(waiting)
                moved = True
        return moved

    def _let_go(self, waiting: _Waiting) -> None:
        self._waiting.pop(waiting.item.key, None)
        self._settled.add(waiting.item.key)
        waiting.resolve()
