import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from openswe.agent_api.git_metadata import GitCheckout, GitSnapshot, GitTarget, TurnGitMetadata
from openswe.sandboxes.scripts import SandboxScript, SandboxScriptError
from tests.tools.test_monitor import LocalShell


@pytest.mark.parametrize("pr", [False, True])
async def test_actual_checkout_wins_over_configured_repo(
    monkeypatch: pytest.MonkeyPatch, pr: bool
) -> None:
    git = TurnGitMetadata(
        {
            "configurable": {
                "invocation_id": "turn",
                "github_login": "different-credential-owner",
                "repo": {"owner": "wrong", "name": "repo"},
            },
            "metadata": {"ls_attribution_identifier": "trigger"},
        }
    )
    before: dict[str, JsonValue] = {
        "repos": {"/workspace/actual": {"repo": "org/actual", "branch": "local", "head": "old"}}
    }
    after: dict[str, JsonValue] = {
        "repos": {"/workspace/actual": {"repo": "org/actual", "branch": "local", "head": "new"}}
    }
    monkeypatch.setattr(
        git,
        "snapshot",
        AsyncMock(side_effect=[GitSnapshot.model_validate(value) for value in (before, after)]),
    )
    state = await git.begin()
    state.update(
        git.evidence([GitTarget(repo="org/actual", branch="exact/PR-head")], "call", pr=True)
        if pr
        else {}
    )
    state["git_trace_pr:previous"] = {
        "invocation": "previous-turn",
        "targets": [{"repo": "org/stale", "branch": "stale"}],
    }
    result = await git.reconcile(state)
    assert result["ls_git_repo"] == "org/actual"
    assert result["ls_git_branch"] == ("exact/PR-head" if pr else "local")
    assert result["ls_attribution_identifier"] == "trigger"
    assert result["ls_repo_provider"] == "github"


async def test_work_order_wins_over_snapshot_order_pr_order_and_completion_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = TurnGitMetadata({"configurable": {"invocation_id": "turn"}})
    before: dict[str, JsonValue] = {
        "repos": {
            "/workspace/a": {"repo": "org/a", "branch": "main", "head": "old"},
            "/workspace/z": {"repo": "org/z", "branch": "local", "head": "old"},
        }
    }
    first: dict[str, JsonValue] = {
        "repos": {
            "/workspace/a": {"repo": "org/a", "branch": "main", "head": "old"},
            "/workspace/z": {"repo": "org/z", "branch": "local", "head": "new"},
        }
    }
    later: dict[str, JsonValue] = {
        "repos": {
            "/workspace/a": {"repo": "org/a", "branch": "later", "head": "new"},
            "/workspace/z": {"repo": "org/z", "branch": "local", "head": "new"},
        }
    }
    monkeypatch.setattr(
        git,
        "snapshot",
        AsyncMock(
            side_effect=[
                GitSnapshot.model_validate(value) for value in (before, before, first, later, later)
            ]
        ),
    )
    baseline = await git.begin()
    assert git.work_baseline(baseline) == GitSnapshot.model_validate(before)
    unchanged = await git.reconcile(baseline)
    assert "ls_git_repo" not in unchanged and "ls_git_branch" not in unchanged
    first_work = await git.work_evidence(GitSnapshot.model_validate(before), "first", 10)
    assert git.work_baseline({**baseline, **first_work}) is None
    later_work = await git.work_evidence(GitSnapshot.model_validate(first), "later", 20)
    state = {
        **baseline,
        **git.evidence(
            [GitTarget(repo="org/a", branch="Later/head")], "pr-a", pr=True, observed_at=30
        ),
        **git.evidence(
            [GitTarget(repo="org/z", branch="Exact/first-head")], "pr-z", pr=True, observed_at=40
        ),
        **later_work,
        **first_work,
    }
    result = await git.reconcile(state)
    assert result["ls_git_repo"] == "org/z"
    assert result["ls_git_branch"] == "Exact/first-head"
    assert result["open_swe_git_targets"] == [
        {"repo": "org/z", "branch": "Exact/first-head"},
        {"repo": "org/a", "branch": "Later/head"},
    ]


async def test_invalid_optional_evidence_does_not_discard_valid_pr_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = "openswe.agent_api.git_metadata"
    monkeypatch.setattr(f"{module}.get_sandbox_backend", AsyncMock())
    monkeypatch.setattr(f"{module}.resolve_sandbox_work_dir", AsyncMock(return_value="/workspace"))
    monkeypatch.setattr(
        f"{module}.SandboxScript.run", AsyncMock(return_value={"repos": {"bad": 42}})
    )
    git = TurnGitMetadata({"configurable": {"thread_id": "thread", "invocation_id": "turn"}})
    assert await git.snapshot() is None
    assert await git.work_evidence(GitSnapshot(), "call", 1) == {}
    result = await git.reconcile(
        {
            "git_trace_baseline": {"invocation": "turn", "snapshot": {"repos": []}},
            "git_trace_work:bad": {"invocation": "turn", "targets": None},
            **git.evidence([GitTarget(repo="org/actual", branch="Exact/head")], "pr", pr=True),
        }
    )
    assert result["ls_git_repo"] == "org/actual"
    assert result["ls_git_branch"] == "Exact/head"


async def test_snapshot_detects_more_edits_to_an_already_dirty_checkout(tmp_path: Path) -> None:
    for name in ("a-no-origin", "b-unborn"):
        path = tmp_path / name
        subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
        if name == "b-unborn":
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/org/unborn.git",
                ],
                check=True,
            )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "work", str(repo)], check=True, capture_output=True)
    for key, value in [("user.name", "Test"), ("user.email", "test@example.com")]:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:org/actual.git"],
        check=True,
    )
    file = repo / "file"
    file.write_text("initial")
    subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True
    )
    script = SandboxScript("git_trace.py", str(tmp_path / "scripts"))
    backend = LocalShell()

    async def snapshot() -> GitCheckout:
        response = await script.run(backend, {"root": str(tmp_path)})
        return GitSnapshot.model_validate(response).repos[str(repo)]

    file.write_text("dirty")
    first = await snapshot()
    file.write_text("different dirty content")
    second = await snapshot()
    assert first.repo == second.repo == "org/actual"
    assert first.branch == second.branch == "work"
    assert first.dirty != second.dirty
    with pytest.raises(SandboxScriptError, match="Could not inspect workspace git state"):
        await script.run(backend, {"root": str(tmp_path / "missing")})
