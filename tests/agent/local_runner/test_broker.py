import asyncio
from typing import Any

import pytest

from agent.local_runner.broker import (
    LocalDeviceUnreachableError,
    LocalRunnerBroker,
    LocalRunnerConnection,
)


def _connection(broker: LocalRunnerBroker, login: str, device_id: str) -> LocalRunnerConnection:
    sent: list[dict[str, Any]] = []

    async def send(frame: dict[str, Any]) -> None:
        sent.append(frame)
        # Answer as the desktop would, on the next tick.
        asyncio.get_running_loop().call_soon(
            connection.resolve, {"id": frame["id"], "type": "exec_result", "output": "hello"}
        )

    connection = LocalRunnerConnection(login, device_id, send)
    connection.sent = sent  # type: ignore[attr-defined]
    broker.register(connection)
    return connection


async def test_a_registered_device_answers_on_this_replica() -> None:
    broker = LocalRunnerBroker()
    connection = _connection(broker, "octocat", "abc123")

    reply = await broker.call("octocat", "abc123", {"type": "exec", "command": "ls"})

    assert reply["output"] == "hello"
    assert connection.sent[0]["command"] == "ls"  # type: ignore[attr-defined]


async def test_one_account_cannot_reach_another_accounts_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = LocalRunnerBroker()
    _connection(broker, "victim", "abc123")
    monkeypatch.setattr(broker, "_rendezvous", _never_arrives)

    with pytest.raises(LocalDeviceUnreachableError):
        await broker.call("attacker", "abc123", {"type": "exec", "command": "ls"})


async def _never_arrives(login: str, device_id: str) -> None:
    raise LocalDeviceUnreachableError(device_id, "no socket opened in time")


async def test_a_device_that_connects_late_resolves_the_waiting_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-replica path: the run waits, the desktop opens a socket here."""
    broker = LocalRunnerBroker()
    wanted: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        "agent.local_runner.broker.wanted_devices",
        _FakeStore(wanted, deleted),
    )

    async def connect_soon() -> None:
        await asyncio.sleep(0.01)
        _connection(broker, "octocat", "abc123")

    task = asyncio.create_task(connect_soon())
    reply = await broker.call("octocat", "abc123", {"type": "exec", "command": "ls"})
    await task

    assert reply["output"] == "hello"
    assert wanted == ["octocat:abc123"]
    assert deleted == ["octocat:abc123"], "the rendezvous record must not linger"


async def test_a_device_that_never_connects_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = LocalRunnerBroker()
    monkeypatch.setattr("agent.local_runner.broker.wanted_devices", _FakeStore([], []))
    monkeypatch.setattr("agent.local_runner.broker.RENDEZVOUS_TIMEOUT_S", 0.01)

    with pytest.raises(LocalDeviceUnreachableError, match="not connected"):
        await broker.call("octocat", "abc123", {"type": "exec", "command": "ls"})


async def test_a_closed_socket_fails_its_in_flight_commands() -> None:
    broker = LocalRunnerBroker()

    async def send(_frame: dict[str, Any]) -> None:
        return None

    connection = LocalRunnerConnection("octocat", "abc123", send)
    broker.register(connection)
    call = asyncio.create_task(broker.call("octocat", "abc123", {"type": "exec"}))
    await asyncio.sleep(0)
    broker.unregister(connection)

    with pytest.raises(LocalDeviceUnreachableError):
        await call


class _FakeStore:
    def __init__(self, wanted: list[str], deleted: list[str]) -> None:
        self.wanted = wanted
        self.deleted = deleted

    async def put(self, key: str, _record: Any) -> None:
        self.wanted.append(key)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
