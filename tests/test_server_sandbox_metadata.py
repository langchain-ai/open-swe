from __future__ import annotations

import asyncio

from agent import server


def test_get_reply_tools_limits_channels_by_source() -> None:
    assert server._get_reply_tools("github") == [server.github_comment]
    assert server._get_reply_tools("slack") == [server.slack_thread_reply]
    assert server._get_reply_tools("linear") == [server.linear_comment]


def test_persist_sandbox_metadata_stores_sandbox_id_and_repo_dir(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, str]) -> None:
            calls.append({"thread_id": thread_id, "metadata": metadata})

    class _FakeClient:
        threads = _FakeThreads()

    monkeypatch.setattr(server, "client", _FakeClient())

    asyncio.run(server._persist_sandbox_metadata("thread-123", "sandbox-123", "/tmp/repo"))

    assert calls == [
        {
            "thread_id": "thread-123",
            "metadata": {"sandbox_id": "sandbox-123", "repo_dir": "/tmp/repo"},
        }
    ]


def test_get_agent_updates_thread_metadata_with_real_sandbox_id(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class _FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append({"thread_id": thread_id, "metadata": metadata})

    class _FakeClient:
        threads = _FakeThreads()

    class _FakeSandbox:
        id = "sandbox-123"

        def execute(self, command: str):
            raise AssertionError("sandbox.execute should not be called in this test")

    class _FakeAgent:
        def with_config(self, config):
            return self

    async def fake_resolve_github_token(config, thread_id: str) -> tuple[str, str]:
        return "github-token", ""

    async def fake_get_sandbox_id_from_metadata(thread_id: str):
        return None

    async def fake_clone_or_pull_repo_in_sandbox(
        sandbox_backend,
        owner: str,
        repo: str,
        github_token,
        branch_name: str | None = None,
    ):
        return "/tmp/repo"

    async def fake_read_agents_md_in_sandbox(sandbox_backend, repo_dir: str) -> str:
        return ""

    monkeypatch.setattr(server, "client", _FakeClient())
    monkeypatch.setattr(server, "resolve_github_token", fake_resolve_github_token)
    monkeypatch.setattr(server, "get_sandbox_id_from_metadata", fake_get_sandbox_id_from_metadata)
    monkeypatch.setattr(server, "create_sandbox", lambda sandbox_id=None: _FakeSandbox())
    monkeypatch.setattr(server, "_clone_or_pull_repo_in_sandbox", fake_clone_or_pull_repo_in_sandbox)
    monkeypatch.setattr(server, "read_agents_md_in_sandbox", fake_read_agents_md_in_sandbox)
    monkeypatch.setattr(server, "create_deep_agent", lambda **kwargs: _FakeAgent())
    monkeypatch.setattr(server, "make_model", lambda *args, **kwargs: "fake-model")
    monkeypatch.setattr(server, "construct_system_prompt", lambda *args, **kwargs: "fake-prompt")
    monkeypatch.setattr(server, "get_config", lambda: {"metadata": {}})
    monkeypatch.setattr(server, "SANDBOX_BACKENDS", {})

    config = {
        "configurable": {
            "thread_id": "thread-123",
            "__is_for_execution__": True,
            "repo": {"owner": "parimple", "name": "BohtPY"},
        },
        "metadata": {},
    }

    asyncio.run(server.get_agent(config))

    assert {"thread_id": "thread-123", "metadata": {"sandbox_id": server.SANDBOX_CREATING}} in updates
    assert {
        "thread_id": "thread-123",
        "metadata": {"sandbox_id": "sandbox-123", "repo_dir": "/tmp/repo"},
    } in updates


def test_clone_or_pull_repo_syncs_clean_repo_to_default_branch(monkeypatch) -> None:
    commands: list[str] = []

    class _FakeSandbox:
        def execute(self, command: str):
            commands.append(command)
            return type("Result", (), {"exit_code": 0, "output": ""})()

    async def fake_work_dir(_sandbox_backend) -> str:
        return "/tmp"

    async def fake_repo_dir(_sandbox_backend, _repo_name: str) -> str:
        return "/tmp/BohtPY"

    async def fake_default_branch(_owner: str, _repo: str, _token: str) -> str:
        return "main"

    monkeypatch.setattr(server, "aresolve_sandbox_work_dir", fake_work_dir)
    monkeypatch.setattr(server, "aresolve_repo_dir", fake_repo_dir)
    monkeypatch.setattr(server, "is_valid_git_repo", lambda sandbox_backend, repo_dir: True)
    monkeypatch.setattr(server, "git_has_uncommitted_changes", lambda sandbox_backend, repo_dir: False)
    monkeypatch.setattr(server, "setup_git_credentials", lambda sandbox_backend, token: None)
    monkeypatch.setattr(server, "cleanup_git_credentials", lambda sandbox_backend: None)
    monkeypatch.setattr(server, "get_github_default_branch", fake_default_branch)

    repo_dir = asyncio.run(
        server._clone_or_pull_repo_in_sandbox(
            _FakeSandbox(),
            "parimple",
            "BohtPY",
            "github-token",
        )
    )

    assert repo_dir == "/tmp/BohtPY"
    assert any(
        "git -c credential.helper='store --file=/tmp/.git-credentials' fetch origin --prune"
        in command
        for command in commands
    )
    assert any("git reset --hard origin/main" in command for command in commands)
    assert any("git clean -fd" in command for command in commands)


def test_clone_or_pull_repo_syncs_clean_repo_to_existing_branch(monkeypatch) -> None:
    commands: list[str] = []

    class _FakeSandbox:
        def execute(self, command: str):
            commands.append(command)
            return type("Result", (), {"exit_code": 0, "output": ""})()

    async def fake_work_dir(_sandbox_backend) -> str:
        return "/tmp"

    async def fake_repo_dir(_sandbox_backend, _repo_name: str) -> str:
        return "/tmp/BohtPY"

    monkeypatch.setattr(server, "aresolve_sandbox_work_dir", fake_work_dir)
    monkeypatch.setattr(server, "aresolve_repo_dir", fake_repo_dir)
    monkeypatch.setattr(server, "is_valid_git_repo", lambda sandbox_backend, repo_dir: True)
    monkeypatch.setattr(server, "git_has_uncommitted_changes", lambda sandbox_backend, repo_dir: False)
    monkeypatch.setattr(server, "setup_git_credentials", lambda sandbox_backend, token: None)
    monkeypatch.setattr(server, "cleanup_git_credentials", lambda sandbox_backend: None)

    repo_dir = asyncio.run(
        server._clone_or_pull_repo_in_sandbox(
            _FakeSandbox(),
            "parimple",
            "BohtPY",
            "github-token",
            branch_name="open-swe/thread-123",
        )
    )

    assert repo_dir == "/tmp/BohtPY"
    assert any("git reset --hard" in command and "origin/open-swe/thread-123" in command for command in commands)
    assert any("git clean -fd" in command for command in commands)
