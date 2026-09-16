import importlib
from typing import Any

import pytest

add_repository_tool = importlib.import_module("agent.tools.add_repository")


class _FakeThreads:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        self.updates.append(metadata)
        self.metadata = {**self.metadata, **metadata}
        return {"thread_id": thread_id, "metadata": self.metadata}


class _FakeClient:
    def __init__(self, threads: _FakeThreads) -> None:
        self.threads = threads


@pytest.fixture
def thread(monkeypatch: pytest.MonkeyPatch) -> _FakeThreads:
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "thread-1", "github_login": "octocat"}},
    )
    monkeypatch.setattr(add_repository_tool, "is_repo_allowed", lambda _repo: True)

    async def allow(_login: str, _full_name: str) -> str:
        return "token"

    monkeypatch.setattr(add_repository_tool, "require_repo_access_for_user", allow)

    async def no_instructions(_owner: str, _repo: str) -> str | None:
        return None

    monkeypatch.setattr(
        "agent.dashboard.agent_instructions.get_repo_agent_instructions", no_instructions
    )

    threads = _FakeThreads({"repos": [{"owner": "acme", "name": "one"}]})
    monkeypatch.setattr(add_repository_tool, "get_client", lambda url: _FakeClient(threads))
    return threads


@pytest.mark.parametrize("full_name", ["", "acme", "https://github.com/acme/two", "acme/two/three"])
async def test_add_repository_rejects_a_malformed_name(
    thread: _FakeThreads, full_name: str
) -> None:
    result = await add_repository_tool.add_repository(full_name)

    assert result["ok"] is False
    assert thread.updates == []


async def test_add_repository_appends_to_the_thread(thread: _FakeThreads) -> None:
    result = await add_repository_tool.add_repository("acme/two", state={"work_dir": "/work"})

    assert result["ok"] is True
    assert result["repos"] == ["acme/one", "acme/two"]
    assert result["already_present"] is False
    assert result["instructions"] is None
    assert result["clone_hint"] == "cd /work && gh repo clone acme/two"
    assert thread.updates == [
        {"repos": [{"owner": "acme", "name": "one"}, {"owner": "acme", "name": "two"}]}
    ]


async def test_add_repository_is_idempotent(thread: _FakeThreads) -> None:
    result = await add_repository_tool.add_repository("Acme/One")

    assert result["ok"] is True
    assert result["already_present"] is True
    assert result["repos"] == ["acme/one"]
    assert "clone_hint" not in result
    assert thread.updates == []
