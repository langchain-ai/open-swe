import subprocess
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from agent.desktop_branch import build_branch_name, rename_temporary_worktree_branch


class _FakeModel:
    def __init__(self, branch: str) -> None:
        self.branch = branch
        self.calls = 0

    def with_structured_output(self, schema: Any) -> Any:
        model = self

        class _Structured:
            async def ainvoke(self, _messages: Any, config: Any = None) -> Any:  # noqa: ARG002
                model.calls += 1
                return schema(branch=model.branch)

        return _Structured()


def _repo(path: Path, branch: str) -> Path:
    path.mkdir()
    for args in (
        ["init", "-q", "-b", branch],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["commit", "-qm", "init", "--allow-empty"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True)
    return path


def _branch(path: Path) -> str:
    return subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_build_branch_name_normalizes_model_output() -> None:
    assert build_branch_name("Fix Flaky Login Test!") == "open-swe/fix-flaky-login-test"
    assert build_branch_name("!!!") is None


async def test_renames_a_temporary_branch_from_the_request(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "worktree", "open-swe/local-abc12345")
    model = _FakeModel("retry flaky login")

    renamed = await rename_temporary_worktree_branch(
        worktree_path=str(repo),
        request="Make the login test stop flaking",
        model=cast(BaseChatModel, model),
    )

    assert renamed == "open-swe/retry-flaky-login"
    assert _branch(repo) == "open-swe/retry-flaky-login"


async def test_leaves_a_deliberately_named_branch_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "worktree", "feature/login")
    model = _FakeModel("retry flaky login")

    assert (
        await rename_temporary_worktree_branch(
            worktree_path=str(repo),
            request="Make the login test stop flaking",
            model=cast(BaseChatModel, model),
        )
        is None
    )
    assert model.calls == 0
    assert _branch(repo) == "feature/login"
