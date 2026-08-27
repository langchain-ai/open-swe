"""Routing between an agent run and the desktop that will execute its commands.

A sandbox is reachable from any replica because its id is an address the whole
fleet can dial. A desktop is not: the socket terminates in one process and the
machine behind it is usually behind NAT, so the run has to be brought to the
socket rather than the other way round.

The fast path is a socket this replica already holds. The slow path publishes a
short-lived *wanted* record that the desktop polls for, then waits for it to
open a socket that lands here. Commands themselves never travel through the
store — only the request to be introduced does.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any, Protocol

from pydantic import BaseModel

from ..store import TypedStore, now_ms

logger = logging.getLogger(__name__)

WANTED_NAMESPACE = ("local-runner", "wanted")
WANTED_TTL_MS = 60_000
RENDEZVOUS_TIMEOUT_S = 25.0
DEFAULT_COMMAND_TIMEOUT_S = 30 * 60
REPLICA_ID = uuid.uuid4().hex


class WantedDevice(BaseModel):
    """A replica asking a desktop to open a socket to it."""

    login: str
    device_id: str
    replica_id: str
    expires_at_ms: int

    @property
    def expired(self) -> bool:
        return now_ms() >= self.expires_at_ms


wanted_devices = TypedStore(WANTED_NAMESPACE, WantedDevice)


class LocalDeviceUnreachableError(RuntimeError):
    """The thread's desktop did not answer this run.

    Says nothing about whether it will answer the next one. Like a sandbox that
    fails to respond, the device holds the only copy of the working tree, so the
    run fails rather than silently continuing somewhere else.
    """

    def __init__(self, device_id: str, cause: str) -> None:
        self.device_id = device_id
        super().__init__(f"Desktop {device_id} is not connected: {cause}")


class CommandRelay(Protocol):
    """The narrow view of the broker a backend needs: send this, get an answer."""

    async def call(
        self,
        login: str,
        device_id: str,
        frame: dict[str, Any],
        *,
        timeout: float = ...,
    ) -> dict[str, Any]: ...


def _wanted_key(login: str, device_id: str) -> str:
    return f"{login}:{device_id}"


class LocalRunnerConnection:
    """One desktop socket, owned by the route handler that accepted it."""

    def __init__(self, login: str, device_id: str, send: Any) -> None:
        self.login = login
        self.device_id = device_id
        self._send = send
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False

    async def request(self, frame: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self._closed:
            raise LocalDeviceUnreachableError(self.device_id, "socket closed")
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({**frame, "id": request_id})
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise LocalDeviceUnreachableError(self.device_id, "command timed out") from exc
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, reply: dict[str, Any]) -> None:
        future = self._pending.pop(str(reply.get("id") or ""), None)
        if future is not None and not future.done():
            future.set_result(reply)

    def close(self, reason: str = "socket closed") -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(LocalDeviceUnreachableError(self.device_id, reason))
        self._pending.clear()


class LocalRunnerBroker:
    """Process-global registry of desktop sockets, keyed within a login.

    ``device_id`` is never an identity on its own: it only names a device
    *inside* an authenticated login, so one account can never address another's
    machine by guessing or colliding on an id.
    """

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], LocalRunnerConnection] = {}
        self._waiters: dict[tuple[str, str], set[asyncio.Future[None]]] = {}

    def register(self, connection: LocalRunnerConnection) -> None:
        key = (connection.login, connection.device_id)
        previous = self._connections.get(key)
        self._connections[key] = connection
        if previous is not None and previous is not connection:
            previous.close("replaced by a newer socket")
        for future in self._waiters.pop(key, set()):
            if not future.done():
                future.set_result(None)

    def unregister(self, connection: LocalRunnerConnection) -> None:
        key = (connection.login, connection.device_id)
        if self._connections.get(key) is connection:
            del self._connections[key]
        connection.close()

    def connection(self, login: str, device_id: str) -> LocalRunnerConnection | None:
        return self._connections.get((login, device_id))

    async def call(
        self,
        login: str,
        device_id: str,
        frame: dict[str, Any],
        *,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_S,
    ) -> dict[str, Any]:
        connection = self._connections.get((login, device_id))
        if connection is None:
            connection = await self._rendezvous(login, device_id)
        reply = await connection.request(frame, timeout)
        if reply.get("type") == "error":
            raise LocalDeviceUnreachableError(device_id, str(reply.get("message") or "refused"))
        return reply

    async def _rendezvous(self, login: str, device_id: str) -> LocalRunnerConnection:
        """Ask the desktop to open a socket to *this* replica, and wait for it."""
        key = (login, device_id)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(key, set()).add(future)
        try:
            await wanted_devices.put(
                _wanted_key(login, device_id),
                WantedDevice(
                    login=login,
                    device_id=device_id,
                    replica_id=REPLICA_ID,
                    expires_at_ms=now_ms() + WANTED_TTL_MS,
                ),
            )
            await asyncio.wait_for(future, timeout=RENDEZVOUS_TIMEOUT_S)
        except TimeoutError as exc:
            raise LocalDeviceUnreachableError(device_id, "no socket opened in time") from exc
        finally:
            waiters = self._waiters.get(key)
            if waiters is not None:
                waiters.discard(future)
                if not waiters:
                    self._waiters.pop(key, None)
            with contextlib.suppress(Exception):
                await wanted_devices.delete(_wanted_key(login, device_id))
        connection = self._connections.get(key)
        if connection is None:
            raise LocalDeviceUnreachableError(device_id, "socket closed before use")
        return connection


runner_broker = LocalRunnerBroker()
