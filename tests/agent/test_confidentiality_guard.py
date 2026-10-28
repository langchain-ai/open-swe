import json
import subprocess
from pathlib import Path
from typing import cast

import pytest
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from openswe.confidentiality import check
from openswe.confidentiality.scan import Segment, SensitiveContext, SensitiveSource
from openswe.middleware import confidentiality_guard as guard
from openswe.sandboxes.state import SandboxBackendProxy

_SLACK = (
    "Heads up: the Initech renewal is blocked until we ship the retry fix for their "
    "nightly export job, do not mention them anywhere public"
)
_TOKEN = "ghp_" + "a1B2c3D4e5" * 4


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "dev@example.com")
    _git(work, "config", "user.name", "Dev")
    (work / "README.md").write_text("hello\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "origin", "main")
    _git(work, "remote", "set-url", "origin", "https://github.com/acme/public-repo.git")
    _git(work, "remote", "set-url", "--push", "origin", str(origin))
    _git(work, "update-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(work, "checkout", "-b", "feature")
    return work


def _commit(repo: Path, message: str, content: str = "retry the export\n") -> None:
    (repo / "export.py").write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


class _Runtime:
    config = {"configurable": {"thread_id": "thread-1"}}


class _Request:
    runtime = _Runtime()

    def __init__(self, command: str) -> None:
        self.tool_call = {"name": "execute", "args": {"command": command}, "id": "call-1"}


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setenv("CONFIDENTIALITY_GUARD", "enforce")
    monkeypatch.delenv("CONFIDENTIALITY_REVIEW_MODEL", raising=False)
    backend = LocalShellBackend(root_dir=str(repo), virtual_mode=False)
    monkeypatch.setitem(guard.SANDBOX_BACKENDS, "thread-1", cast(SandboxBackendProxy, backend))

    async def public(full_name: str) -> bool:
        return full_name != "acme/private-repo"

    async def context(_cfg: object, *, target: str | None) -> SensitiveContext:
        return SensitiveContext(
            [SensitiveSource("slack_thread", _SLACK)], private_repositories=["acme/private-repo"]
        )

    monkeypatch.setattr(check, "repository_is_public", public)
    monkeypatch.setattr(guard, "repository_is_public", public)
    monkeypatch.setattr(check, "run_sensitive_context", context)


async def _run(command: str) -> tuple[ToolMessage | None, bool]:
    ran: list[bool] = []

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        ran.append(True)
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await guard.ConfidentialityGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(command)), handler
    )
    return (None if ran else cast(ToolMessage, result)), bool(ran)


async def test_push_of_a_leaked_token_to_a_public_repo_is_refused(repo: Path) -> None:
    _commit(repo, "Retry exports", content=f"TOKEN = '{_TOKEN}'\n")

    refusal, ran = await _run(f"git -C {repo} push origin feature")

    assert not ran and refusal is not None
    payload = json.loads(str(refusal.content))
    assert payload["error_type"] == "ConfidentialContentBlocked"
    assert payload["findings"] == ["file export.py: secret (github_token)"]
    assert _TOKEN not in str(refusal.content)


async def test_commit_message_copied_from_slack_is_refused_without_echoing_it(repo: Path) -> None:
    _commit(repo, f"Fix export\n\n{_SLACK}")

    refusal, ran = await _run(f"git -C {repo} push -u origin feature")

    assert not ran and refusal is not None
    assert "context_overlap (slack_thread)" in str(refusal.content)
    assert "Initech" not in str(refusal.content)


async def test_neutral_push_runs(repo: Path) -> None:
    _commit(repo, "Retry the nightly export when the upstream times out")

    refusal, ran = await _run(f"git -C {repo} push origin feature")

    assert ran and refusal is None


async def test_shadow_mode_never_refuses(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setenv("CONFIDENTIALITY_GUARD", "shadow")
    _commit(repo, "Retry exports", content=f"TOKEN = '{_TOKEN}'\n")

    _refusal, ran = await _run(f"git -C {repo} push origin feature")

    assert ran


async def test_chained_push_to_a_public_repo_is_refused(repo: Path) -> None:
    refusal, ran = await _run(f"cd {repo} && git commit --allow-empty -m wip && git push")

    assert not ran and refusal is not None
    assert json.loads(str(refusal.content))["error_type"] == "ConfidentialityInspectionError"


async def test_gh_comment_mentioning_a_private_repo_is_refused() -> None:
    refusal, ran = await _run(
        "gh pr comment 12 -R acme/public-repo --body 'Ported from acme/private-repo'"
    )

    assert not ran and refusal is not None
    assert "private_repository" in str(refusal.content)


async def test_gh_comment_on_a_private_repo_runs() -> None:
    _refusal, ran = await _run(f"gh pr comment 12 -R acme/private-repo --body '{_SLACK}'")

    assert ran


def test_text_already_public_in_the_target_is_not_overlap() -> None:
    context = SensitiveContext([SensitiveSource("slack_thread", _SLACK)], public_text=_SLACK)

    assert context.scan([Segment("pull request body", _SLACK)]) == []
