from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.agent_api import graph
from openswe.agent_api.graph import RunSession

CONFIG = {"configurable": {"thread_id": "0b9e2d6c-6a3b-5f0e-9a52-2f3d1c4b5a69"}}


def _session(*, managed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        config=CONFIG,
        assistant_id="agent",
        sandbox=AsyncMock(return_value=object() if managed else None),
    )


def _prepare(sandbox_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(sandbox_id=sandbox_id, sandbox_error=None)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    bound, reconnected = AsyncMock(), AsyncMock()
    monkeypatch.setattr(graph, "bind_mda_sandbox", bound)
    monkeypatch.setattr(graph, "reconnect_sandbox", reconnected)
    return SimpleNamespace(bound=bound, reconnected=reconnected)


async def test_a_sandbox_mda_opened_gets_its_github_proxy_before_the_agent_runs(
    monkeypatch: pytest.MonkeyPatch, calls: SimpleNamespace
) -> None:
    monkeypatch.setattr(graph, "proxy_token_recorded", lambda _thread_id: False)

    await RunSession.bind_sandbox(_session(managed=True), _prepare("sandbox-1"))

    calls.bound.assert_awaited_once()
    calls.reconnected.assert_awaited_once_with(CONFIG, "agent")


@pytest.mark.parametrize(
    ("managed", "sandbox_id", "recorded"),
    [
        (True, "sandbox-1", True),  # the proxy is already set in this process
        (True, None, False),  # MDA could not open the sandbox
        (False, "sandbox-1", False),  # not MDA's sandbox
    ],
)
async def test_the_proxy_is_left_alone_otherwise(
    monkeypatch: pytest.MonkeyPatch,
    calls: SimpleNamespace,
    managed: bool,
    sandbox_id: str | None,
    recorded: bool,
) -> None:
    monkeypatch.setattr(graph, "proxy_token_recorded", lambda _thread_id: recorded)

    await RunSession.bind_sandbox(_session(managed=managed), _prepare(sandbox_id))

    calls.reconnected.assert_not_awaited()


async def test_a_failed_connection_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, calls: SimpleNamespace
) -> None:
    monkeypatch.setattr(graph, "proxy_token_recorded", lambda _thread_id: False)
    calls.reconnected.side_effect = RuntimeError("sandbox unreachable")

    await RunSession.bind_sandbox(_session(managed=True), _prepare("sandbox-1"))
