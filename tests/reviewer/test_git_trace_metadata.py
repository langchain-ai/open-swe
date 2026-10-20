import base64
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import JsonValue

from openswe.remote_runtime.git_metadata import (
    GitCheckout,
    GitSnapshot,
    GitTarget,
    TurnGitMetadata,
    record_pr_target,
)
from openswe.remote_runtime.graph import RemoteGraph
from openswe.remote_runtime.runs import ModelSpec, RunModels
from openswe.remote_runtime.tokens import RemoteRun
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface


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


@pytest.mark.parametrize(
    "targets",
    [
        [],
        [GitTarget(repo="org/z", branch="first"), GitTarget(repo="org/a", branch="later")],
        [GitTarget(repo="org/a", branch="z-first"), GitTarget(repo="org/a", branch="a-later")],
    ],
)
async def test_first_target_populates_single_valued_git_keys(
    monkeypatch: pytest.MonkeyPatch, targets: list[GitTarget]
) -> None:
    git = TurnGitMetadata({"configurable": {"invocation_id": "turn"}})
    snapshot = {"repos": {"/workspace/a": {"repo": "org/a", "branch": "unchanged"}}}
    monkeypatch.setattr(
        git, "snapshot", AsyncMock(return_value=GitSnapshot.model_validate(snapshot))
    )
    state = {**await git.begin(), **git.evidence(targets, "call", pr=True)}
    result = await git.reconcile(state)
    assert result.get("ls_git_repo") == (targets[0].repo if targets else None)
    assert result.get("ls_git_branch") == (targets[0].branch if targets else None)
    assert result.get("open_swe_git_targets", []) == [target.model_dump() for target in targets]


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
                GitSnapshot.model_validate(value) for value in (before, first, later, later)
            ]
        ),
    )
    baseline = await git.begin()
    first_work = await git.work_evidence(GitSnapshot.model_validate(before), "first", 10)
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
    module = "openswe.remote_runtime.git_metadata"
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


async def test_tool_observation_stops_scanning_after_first_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = GitSnapshot(
        repos={
            f"/workspace/{name}": GitCheckout(repo=f"org/{name}", branch="local", head="old")
            for name in ("a", "z")
        }
    )
    scans = 0

    async def snapshot(self: TurnGitMetadata) -> GitSnapshot:
        nonlocal scans
        scans += 1
        return current.model_copy(deep=True)

    @tool
    async def execute(repo: str) -> str:
        """Simulate work in a checkout or a structured PR outcome."""
        if repo == "pr":
            record_pr_target("org/z", "Exact/head")
        elif repo:
            current.repos[f"/workspace/{repo}"].head += "-edited"
        return "done"

    async def build(config: RunnableConfig, surface: ToolSurface) -> None:
        surface.graph = create_agent(model=FakeListChatModel(responses=["done"]), tools=[execute])

    async def models(config: RunConfig) -> RunModels:
        spec = ModelSpec(model_id="test", kwargs={})
        return RunModels(model=spec, subagent_model=spec, use_gateway=False)

    monkeypatch.setattr(TurnGitMetadata, "snapshot", snapshot)
    monkeypatch.setattr(
        "openswe.remote_runtime.graph.ManagedSandbox.for_thread", AsyncMock(return_value=None)
    )
    graph = RemoteGraph("agent", build, models)
    run = RemoteRun(
        thread_id="thread", assistant_id="agent", configurable={"invocation_id": "turn"}
    )
    prepared = await graph.hook(run, "prepare", {"run_id": "run"})
    assert isinstance(prepared, dict)
    state = prepared["update"]
    assert isinstance(state, dict)
    for index, repo in enumerate(("", "z", "a", "z", "pr")):
        reply = await graph.hook(
            run,
            "call",
            {
                "run_id": "run",
                "state": state,
                "name": "execute",
                "arguments": {"repo": repo},
                "tool_call_id": f"call-{index}",
            },
        )
        assert isinstance(reply, dict)
        update = reply["update"]
        assert isinstance(update, dict)
        state.update(update)
        message = reply["message"]
        assert isinstance(message, dict)
        data = message["data"]
        assert isinstance(data, dict)
        assert data["content"] == "done"
    reconciled = await graph.hook(run, "after_agent", {"run_id": "run", "state": state})
    assert isinstance(reconciled, dict)
    assert reconciled["trace_metadata"]["ls_git_repo"] == "org/z"
    assert reconciled["trace_metadata"]["ls_git_branch"] == "Exact/head"
    assert reconciled["trace_metadata"]["open_swe_git_targets"] == [
        {"repo": "org/z", "branch": "Exact/head"},
        {"repo": "org/a", "branch": "local"},
    ]
    assert reconciled["update"]["git_trace_baseline"] is None
    assert scans == 4


def test_snapshot_detects_more_edits_to_an_already_dirty_checkout(tmp_path: Path) -> None:
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
    script = Path(__file__).resolve().parents[2] / "openswe/sandbox_scripts/git_trace.py"
    payload = base64.b64encode(json.dumps({"root": str(tmp_path)}).encode()).decode()

    def snapshot() -> dict[str, object]:
        response = subprocess.run(
            [sys.executable, str(script), payload], check=True, capture_output=True
        )
        return json.loads(response.stdout)["repos"][str(repo)]

    file.write_text("dirty")
    first = snapshot()
    file.write_text("different dirty content")
    second = snapshot()
    assert first["repo"] == second["repo"] == "org/actual"
    assert first["branch"] == second["branch"] == "work"
    assert first["dirty"] != second["dirty"]
