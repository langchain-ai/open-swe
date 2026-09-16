import base64
import importlib
from typing import Any

import pytest

from agent.github.repositories import Repository
from tests.support.repositories import FakeRepositories

add_repos_tool = importlib.import_module("agent.tools.add_repos")

_SENTINEL = "===OPEN-SWE-REPO==="


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _block(**fields: str) -> str:
    body = "\n".join(f"{key}={value}" for key, value in fields.items())
    return f"{_SENTINEL}\n{body}\n"


class _FakeExecute:
    def __init__(self, output: str) -> None:
        self.output = output
        self.exit_code = 0


class _FakeBackend:
    def __init__(self) -> None:
        self.output = ""
        self.commands: list[str] = []

    async def aexecute(self, command: str, *, timeout: int | None = None) -> _FakeExecute:
        self.commands.append(command)
        return _FakeExecute(self.output)

    async def ready(self) -> None:
        return None


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


class _Harness:
    def __init__(
        self,
        threads: _FakeThreads,
        backend: _FakeBackend,
        repositories: FakeRepositories,
        one: Repository,
    ) -> None:
        self.threads = threads
        self.backend = backend
        self.repositories = repositories
        self.one = one

    def id_of(self, full_name: str) -> str:
        return str(self.repositories.rows[full_name.lower()].id)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, fake_repository_writes: FakeRepositories) -> _Harness:
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "thread-1", "github_login": "octocat"}},
    )
    monkeypatch.setattr(add_repos_tool, "is_repo_allowed", lambda _repo: True)

    async def allow(_login: str, _full_name: str) -> str:
        return "token"

    monkeypatch.setattr(add_repos_tool, "require_repo_access_for_user", allow)

    async def no_instructions(_owner: str, _repo: str) -> str | None:
        return None

    monkeypatch.setattr(
        "agent.dashboard.agent_instructions.get_repo_agent_instructions", no_instructions
    )

    one = fake_repository_writes.add("acme/one")
    threads = _FakeThreads({"repository_ids": [str(one.id)]})
    monkeypatch.setattr(add_repos_tool, "get_client", lambda url: _FakeClient(threads))

    backend = _FakeBackend()

    async def sandbox(_thread_id: str) -> _FakeBackend:
        return backend

    async def work_dir(_backend: _FakeBackend) -> str:
        return "/work"

    monkeypatch.setattr(add_repos_tool, "get_sandbox_backend", sandbox)
    monkeypatch.setattr(add_repos_tool, "resolve_sandbox_work_dir", work_dir)
    return _Harness(threads, backend, fake_repository_writes, one)


_VERIFIED_ONE = _block(
    full_name="acme/one",
    path="/work/one",
    remote_url="git@github.com:Acme/One.git",
    remote_matches="1",
    action="verified",
    branch="open-swe/fix-thing",
    detached="0",
    head_sha="a" * 40,
    default_branch="main",
    upstream="origin/open-swe/fix-thing",
    behind="2",
    ahead="3",
    status_b64=_b64("A  new.py\n M agent/one.py\n?? notes.txt\n"),
    stash_count="1",
    last_commit_b64=_b64(
        "\x1f".join(
            [
                "b" * 40,
                "Ada Lovelace",
                "ada@example.com",
                "2026-09-16T10:00:00-04:00",
                "2026-09-16T10:05:00-04:00",
                "fix: do the thing",
            ]
        )
    ),
    commit_count="412",
    describe="v1.2.3-4-gbbbbbbb",
    branches_b64=_b64("open-swe/fix-thing\nmain\n"),
    last_fetch_epoch="1789569600",
    submodules="0",
    has_agents_md="1",
    has_claude_md="0",
)

_CLONED_TWO = _block(
    full_name="acme/two",
    path="/work/two",
    action="cloned",
    remote_url="https://github.com/acme/two.git",
    remote_matches="1",
    branch="main",
    detached="0",
    head_sha="c" * 40,
    default_branch="main",
    upstream="origin/main",
    behind="0",
    ahead="0",
    status_b64=_b64(""),
    stash_count="0",
    last_commit_b64=_b64(
        "\x1f".join(
            [
                "d" * 40,
                "Grace Hopper",
                "grace@example.com",
                "2026-09-15T09:00:00-04:00",
                "2026-09-15T09:00:00-04:00",
                "chore: initial",
            ]
        )
    ),
    branches_b64=_b64("main\n"),
    submodules="0",
    has_agents_md="0",
    has_claude_md="0",
)


async def test_rejects_a_malformed_name_and_keeps_the_rest(harness: _Harness) -> None:
    harness.backend.output = _VERIFIED_ONE + _CLONED_TWO

    result = await add_repos_tool.add_repos.ainvoke(
        {"full_names": ["acme", "acme/two", "acme/three/deep"]}
    )

    assert result["ok"] is True
    assert result["rejected"] == [
        {"full_name": "acme", "reason": "not a simple owner/name repository string"},
        {"full_name": "acme/three/deep", "reason": "not a simple owner/name repository string"},
    ]
    assert result["added"] == ["acme/two"]
    assert [report["full_name"] for report in result["repos"]] == ["acme/one", "acme/two"]
    assert [report["id"] for report in result["repos"]] == [
        harness.id_of("acme/one"),
        harness.id_of("acme/two"),
    ]


async def test_appends_a_new_repo_and_reports_it_as_cloned(harness: _Harness) -> None:
    harness.backend.output = _VERIFIED_ONE + _CLONED_TWO

    result = await add_repos_tool.add_repos.ainvoke({"full_names": ["acme/two"]})

    assert result["ok"] is True
    assert result["work_dir"] == "/work"
    assert result["added"] == ["acme/two"]
    assert result["already_present"] == []
    assert harness.threads.updates == [
        {"repository_ids": [harness.id_of("acme/one"), harness.id_of("acme/two")]}
    ]
    assert harness.repositories.rows["acme/two"].default_branch == "main"

    cloned = result["repos"][1]
    assert cloned["action"] == "cloned"
    assert cloned["path"] == "/work/two"
    assert cloned["working_tree"] == {
        "clean": True,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "conflicted": 0,
        "files": [],
    }
    assert cloned["last_commit"]["subject"] == "chore: initial"
    assert cloned["custom_instructions"] is None
    assert "repo_block acme/two /work/two" in harness.backend.commands[0]


async def test_reports_an_existing_matching_checkout_as_verified(harness: _Harness) -> None:
    harness.backend.output = _VERIFIED_ONE

    result = await add_repos_tool.add_repos.ainvoke({"full_names": ["Acme/One"]})

    assert result["already_present"] == ["Acme/One"]
    assert harness.threads.updates == []

    [report] = result["repos"]
    assert report["action"] == "verified"
    assert report["remote_matches"] is True
    assert report["branch"] == "open-swe/fix-thing"
    assert report["detached"] is False
    assert report["upstream"] == "origin/open-swe/fix-thing"
    assert (report["ahead"], report["behind"]) == (3, 2)
    assert report["working_tree"] == {
        "clean": False,
        "staged": 1,
        "unstaged": 1,
        "untracked": 1,
        "conflicted": 0,
        "files": ["A  new.py", " M agent/one.py", "?? notes.txt"],
    }
    assert report["stash_count"] == 1
    assert report["last_commit"] == {
        "sha": "b" * 40,
        "author_name": "Ada Lovelace",
        "author_email": "ada@example.com",
        "authored_at": "2026-09-16T10:00:00-04:00",
        "committed_at": "2026-09-16T10:05:00-04:00",
        "subject": "fix: do the thing",
    }
    assert report["local_branches"] == ["open-swe/fix-thing", "main"]
    assert report["open_swe_branches"] == ["open-swe/fix-thing"]
    assert report["has_agents_md"] is True
    assert report["commit_count"] == 412
    assert report["describe"] == "v1.2.3-4-gbbbbbbb"
    assert report["last_fetch_at"] == "2026-09-16T14:40:00+00:00"


async def test_reports_a_foreign_remote_as_conflict_without_touching_metadata(
    harness: _Harness,
) -> None:
    harness.backend.output = _block(
        full_name="acme/one",
        path="/work/one",
        remote_url="git@github.com:other/one.git",
        action="conflict",
        remote_matches="0",
    )

    result = await add_repos_tool.add_repos.ainvoke({"full_names": ["acme/one"]})

    assert result["ok"] is True
    assert harness.threads.updates == []
    [report] = result["repos"]
    assert report["action"] == "conflict"
    assert report["remote_url"] == "git@github.com:other/one.git"
    assert report["remote_matches"] is False
    assert report["working_tree"] is None


async def test_an_empty_request_still_reports_every_thread_repo(harness: _Harness) -> None:
    harness.backend.output = _VERIFIED_ONE

    result = await add_repos_tool.add_repos.ainvoke({"full_names": []})

    assert result["ok"] is True
    assert result["added"] == []
    assert result["rejected"] == []
    assert harness.threads.updates == []
    assert [report["full_name"] for report in result["repos"]] == ["acme/one"]
    assert result["repos"][0]["action"] == "verified"


async def test_a_silent_sandbox_reports_the_repo_as_an_error(harness: _Harness) -> None:
    harness.backend.output = ""

    result = await add_repos_tool.add_repos.ainvoke({"full_names": []})

    [report] = result["repos"]
    assert report["action"] == "error"
    assert report["path"] == "/work/one"
    assert report["error"] == "The sandbox reported nothing for this repository"
