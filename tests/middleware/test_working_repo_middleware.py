from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.working_repo import (
    WorkingRepoMiddleware,
    discover_working_repo,
    github_repo_from_remote,
)


class FakeBackend:
    def __init__(self, output: str) -> None:
        self.output = output
        self.commands: list[str] = []
        self._open_swe_resolved_work_dir = "/workspace"

    async def aexecute(self, command: str, *, timeout: int) -> Any:
        self.commands.append(command)
        return SimpleNamespace(output=self.output, exit_code=0)


class FakeThreads:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/langchain-ai/open-swe.git", ("langchain-ai", "open-swe")),
        ("git@github.com:langchain-ai/open-swe.git", ("langchain-ai", "open-swe")),
        ("ssh://git@github.com/langchain-ai/open-swe", ("langchain-ai", "open-swe")),
        ("https://gitlab.com/langchain-ai/open-swe", None),
        ("https://github.com/langchain-ai/open-swe/extra", None),
    ],
)
def test_github_repo_from_remote(remote: str, expected: tuple[str, str] | None) -> None:
    assert github_repo_from_remote(remote) == expected


async def test_discover_working_repo_returns_single_unique_repo() -> None:
    backend = FakeBackend(
        "https://github.com/langchain-ai/open-swe.git\ngit@github.com:langchain-ai/open-swe.git\n"
    )

    assert await discover_working_repo(backend) == ("langchain-ai", "open-swe")
    assert 'git -C "$d" remote get-url origin' in backend.commands[0]


async def test_discover_working_repo_rejects_ambiguous_repos() -> None:
    backend = FakeBackend(
        "https://github.com/langchain-ai/open-swe.git\n"
        "https://github.com/langchain-ai/langchain.git\n"
    )

    assert await discover_working_repo(backend) is None


@pytest.mark.parametrize("tool_name", ["execute", "task"])
async def test_sandbox_tool_updates_thread_repo_metadata_once(tool_name: str) -> None:
    backend = FakeBackend("https://github.com/langchain-ai/open-swe.git\n")
    threads = FakeThreads()
    middleware = WorkingRepoMiddleware(
        thread_id="thread-1",
        backend=backend,
        thread_client=SimpleNamespace(threads=threads),
    )
    request: Any = SimpleNamespace(tool_call={"name": tool_name, "args": {}, "id": "call-1"})

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-1")

    await middleware.awrap_tool_call(request, handler)
    await middleware.awrap_tool_call(request, handler)

    assert threads.updates == [
        {
            "thread_id": "thread-1",
            "metadata": {"working_repo_full_name": "langchain-ai/open-swe"},
        }
    ]
