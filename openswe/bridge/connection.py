"""A run's round trips to its owner's desktop through the bridge queue.

Each one queues a request, waits for the desktop to answer it, and hands back the
desktop's result.

A waiter subscribes before it enqueues, so the wake-up it is waiting for cannot
land in the gap between the two, and it re-reads the row on every liveness tick
anyway — a notification that never arrives costs latency only. The liveness tick
is also what keeps a run from waiting out an approval on a desktop that went away.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Final, Self

from openswe.bridge import listener
from openswe.bridge.constants import CLOSED_EVENT, WAIT_GRACE_SECONDS
from openswe.bridge.protocol import BridgeMethod, JsonObject, LocalActionParams
from openswe.bridge.store import Bridge, BridgeStore, BridgeUnavailableError
from openswe.run_config import RunConfig

_LIVENESS_TICK_SECONDS: Final = 15.0
# How long the desktop's approval dialog may wait on the person, past the action's own timeout.
_APPROVAL_WAIT_SECONDS: Final = 120


class BridgeConnection:
    """One run's connection to the desktop serving a bridge."""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge
        self._bridge_id = bridge.bridge_id

    @classmethod
    async def connect(cls, bridge_id: str) -> Self:
        """Bind to a live bridge for its owner's run, or refuse the run."""
        bridge = await BridgeStore.load(bridge_id)
        if bridge is None or not bridge.is_alive:
            raise BridgeUnavailableError(bridge_id, "the desktop app is not connected")
        connection = cls(bridge)
        connection._authorize_run()
        return connection

    async def local_action(self, params: LocalActionParams) -> JsonObject:
        return await self._round_trip(
            "local_action",
            params.dump(),
            wait=params.timeout + _APPROVAL_WAIT_SECONDS + WAIT_GRACE_SECONDS,
        )

    async def _is_alive(self) -> bool:
        bridge = await BridgeStore.load(self._bridge_id)
        return bridge is not None and bridge.is_alive

    def _authorize_run(self) -> None:
        """Refuse a run that anyone but the desktop's owner started.

        Checked on every request, because a connection outlives the run that opened it.
        """
        if not self._bridge.serves(RunConfig.from_runtime().sender_id):
            raise PermissionError("Only the owner of this desktop can act on it")

    async def _round_trip(
        self, method: BridgeMethod, params: JsonObject, *, wait: float
    ) -> JsonObject:
        self._authorize_run()
        async with listener.subscribe(self._bridge_id) as events:
            request_id = await BridgeStore.enqueue(self._bridge_id, method=method, params=params)
            return await self._await_result(events, request_id, wait=wait)

    async def _await_result(
        self,
        events: AsyncIterator[listener.BridgeEvent],
        request_id: str,
        *,
        wait: float,
    ) -> JsonObject:
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            outcome = await self._finished_outcome(request_id)
            if outcome is not None:
                return outcome
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await BridgeStore.complete(
                    self._bridge_id, request_id, error="timed out waiting for the desktop"
                )
                raise TimeoutError(f"The desktop did not answer within {wait:.0f}s")
            if not await self._is_alive():
                # Closing fails the request in the same transaction that marks the
                # bridge closed, so a re-read carries the real reason.
                outcome = await self._finished_outcome(request_id)
                if outcome is not None:
                    return outcome
                raise BridgeUnavailableError(self._bridge_id, "the desktop app disconnected")
            try:
                async with asyncio.timeout(min(_LIVENESS_TICK_SECONDS, remaining)):
                    async for woken_id, event in events:
                        if event == CLOSED_EVENT or woken_id == request_id:
                            break
            except TimeoutError:
                continue

    async def _finished_outcome(self, request_id: str) -> JsonObject | None:
        outcome = await BridgeStore.outcome(self._bridge_id, request_id)
        if outcome is None:
            raise BridgeUnavailableError(
                self._bridge_id, "the request disappeared before it was answered"
            )
        if not outcome.finished:
            return None
        if outcome.error is not None:
            raise RuntimeError(outcome.error)
        if outcome.result is None:
            raise RuntimeError("the desktop answered without a result")
        return outcome.result
