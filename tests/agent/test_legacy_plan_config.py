import asyncio
from typing import Any

import pytest

from agent.threads import runs as thread_runs
from tests.conftest import patch_thread_module


class _FakeThreadsClient:
    async def create(
        self, *, thread_id: str, metadata: dict[str, Any], if_exists: str
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": {}}


class _FakeRunsClient:
    def __init__(self) -> None:
        self.configurable: dict[str, Any] | None = None

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: dict[str, Any],
        config: dict[str, Any],
        if_not_exists: str = "reject",
        stream_mode: list[str] | None = None,
        stream_resumable: bool = False,
    ) -> dict[str, str]:
        self.configurable = config["configurable"]
        return {"run_id": "run-id"}


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreadsClient()
        self.runs = _FakeRunsClient()


@pytest.fixture
def dashboard_run_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangGraphClient:
    client = _FakeLangGraphClient()

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, Any]) -> str:
        return "octo@example.com"

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    return client


def _run_start_command(plan_mode: bool | None) -> dict[str, Any]:
    configurable: dict[str, Any] = {}
    if plan_mode is not None:
        configurable["plan_mode"] = plan_mode
    return {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"role": "user", "content": "do work"}]},
            "config": {"configurable": configurable},
        },
    }


@pytest.mark.parametrize("legacy_mode", [True, False, None])
def test_run_start_ignores_legacy_plan_mode(
    dashboard_run_client: _FakeLangGraphClient,
    legacy_mode: bool | None,
) -> None:
    enriched = asyncio.run(
        thread_runs._enrich_run_start_command(
            "thread-id",
            "octo",
            _run_start_command(legacy_mode),
            metadata={"source": "dashboard", "github_login": "octo", "plan_mode": True},
            creating=False,
        )
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert "plan_mode" not in configurable


@pytest.mark.parametrize("legacy_mode", [True, False])
def test_persisted_turn_with_legacy_plan_field_remains_readable(legacy_mode: bool) -> None:
    from agent.transcript.events import TurnRequested

    event = TurnRequested.model_validate(
        {
            "type": "turn.requested",
            "turn_id": "a113163d-6e0a-44c7-8ab7-0ed726c79803",
            "message_id": "message-1",
            "text": "Plan the work",
            "sender": {"login": "alice", "kind": "dashboard"},
            "plan_mode": legacy_mode,
        }
    )
    assert event.text == "Plan the work"
    assert "plan_mode" not in event.model_dump()
