import asyncio
import json
import shlex
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from runtime_api.operations import ShellVerdict

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.run_parts import RunParts
from openswe.agent_api.tokens import RemoteRun
from openswe.middleware import workflow_push_guard as guard
from openswe.sandboxes import connect, state
from openswe.sandboxes.state import SandboxBackendProxy
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.threads import workflow_approval, workflow_approval_api


class _Response:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code
        self.truncated = False


class _Backend:
    id = "sandbox-id"

    def __init__(
        self,
        *,
        workflow_files: str = ".github/workflows/ci.yml",
        inherited_from_main: bool = False,
        workflows_changed_by_merge: bool = True,
        inspectable: bool = True,
        pending_workflows: bool = False,
    ) -> None:
        self.workflow_files = workflow_files
        self.inherited_from_main = inherited_from_main
        self.workflows_changed_by_merge = workflows_changed_by_merge
        self.inspectable = inspectable
        self.pending_workflows = pending_workflows
        self.commands: list[str] = []
        self.head = "a" * 40

    async def aexecute(self, command: str, *, timeout: int | None = None) -> _Response:
        self.commands.append(command)
        if not self.inspectable:
            return _Response("fatal: not a git repository", 128)
        if "status --porcelain" in command:
            return _Response(" M .github/workflows/x.yml" if self.pending_workflows else "")
        if "rev-parse --show-toplevel" in command:
            return _Response("/repo\n")
        if "rev-parse --verify refs/remotes/origin/feature" in command:
            return _Response("", 1)
        if "symbolic-ref --short refs/remotes/origin/HEAD" in command:
            return _Response("origin/main\n")
        if "rev-parse --symbolic-full-name" in command:
            return _Response("origin/main\n")
        if "rev-list --parents -n 1" in command:
            if self.inherited_from_main:
                return _Response(f"{self.head} {'b' * 40} {'c' * 40}\n")
            return _Response(f"{self.head} {'b' * 40}\n")
        if "merge-base --is-ancestor" in command:
            return _Response("", 0 if self.inherited_from_main else 1)
        if f"diff --quiet {'b' * 40} {self.head}" in command:
            return _Response("", 1 if self.workflows_changed_by_merge else 0)
        if f"diff --quiet {'c' * 40} {self.head}" in command:
            return _Response("", 0 if self.inherited_from_main else 1)
        if f"merge-base {self.head} origin/main" in command:
            return _Response("base-sha\n")
        if "diff --name-only" in command:
            return _Response(f"{self.workflow_files}\n" if self.workflow_files else "")
        if "diff --binary --full-index" in command:
            return _Response(
                "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n+new\n-old\n"
            )
        if "diff --numstat" in command:
            return _Response("1\t1\t.github/workflows/ci.yml\n")
        if "config --get remote.origin.url" in command:
            return _Response("git@github.com:langchain-ai/open-swe.git\n")
        if "rev-parse --abbrev-ref HEAD" in command:
            return _Response("feature\n")
        if any(
            f"rev-parse {ref}" in command
            for ref in ("HEAD", "feature", "refs/heads/feature", "evil")
        ):
            return _Response(f"{self.head}\n")
        return _Response("")


class _Runtime:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    }


class _Request:
    runtime = _Runtime()

    def __init__(
        self, command: str = "git -C /repo push origin feature", name: str = "execute"
    ) -> None:
        self.tool_call = {
            "name": name,
            "args": {"command": command},
            "id": "call-1",
        }

    def override(self, **kwargs: Any) -> _Request:
        next_request = _Request()
        next_request.tool_call = kwargs.get("tool_call", self.tool_call)
        return next_request


@pytest.fixture(autouse=True)
def _clear_backend_cache() -> Any:
    guard.SANDBOX_BACKENDS.clear()
    yield
    guard.SANDBOX_BACKENDS.clear()


def test_parse_git_push_supports_git_c_and_cd() -> None:
    assert guard._parse_git_push("git -C /repo push origin feature") == guard.ParsedGitPush(
        repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
    )
    assert guard._parse_git_push(
        "cd /repo && git push -u origin HEAD:feature"
    ) == guard.ParsedGitPush(
        repo_dir="/repo",
        remote="origin",
        local_ref="HEAD",
        remote_ref="feature",
        set_upstream=True,
    )
    assert guard._parse_git_push("git status && git push") is None
    assert guard._parse_git_push("git push origin feature; git push origin evil:feature") is None


@pytest.mark.parametrize(
    "options",
    [
        "--force",
        "--force-with-lease=",
        "--force-with-lease=../feature",
        "--force-with-lease=feature:not-a-sha",
        "--force-with-lease --force-with-lease",
        "--force-with-lease --mirror",
    ],
)
def test_parse_git_push_rejects_unsafe_options(options: str) -> None:
    assert guard._parse_git_push(f"git -C /repo push {options} origin feature") is None


async def test_workflow_change_for_push_fingerprints_workflow_diff() -> None:
    backend = _Backend()
    change = await guard._workflow_change_for_push(
        backend,
        guard.ParsedGitPush(
            repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
        ),
    )

    assert change is not None
    assert change.repo == "https://github.com/langchain-ai/open-swe"
    assert change.branch == "feature"
    assert change.files == [".github/workflows/ci.yml"]
    assert change.diff_stats == {"files": 1, "additions": 1, "deletions": 1}
    assert change.diff_preview_truncated is False
    assert change.inherited_from is None
    assert "diff --git" in change.diff_preview
    assert (
        change.fixed_command
        == "git -C /repo push origin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/feature"
    )
    assert len(change.fingerprint) == 64
    lease_change = await guard._workflow_change_for_push(
        backend,
        guard.ParsedGitPush(
            repo_dir="/repo",
            remote="origin",
            local_ref="feature",
            remote_ref="feature",
            force_with_lease="--force-with-lease",
        ),
    )
    assert lease_change is not None
    assert lease_change.fingerprint != change.fingerprint


class _GitBackend:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def aexecute(self, command: str, *, timeout: int | None = None) -> _Response:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return _Response(output.decode(), process.returncode or 0)

    async def git(self, args: str) -> str:
        result = await self.aexecute(f"git {args}")
        assert result.exit_code == 0, result.output
        return result.output.strip()


@pytest.mark.parametrize(
    "base",
    ["fallback", "upstream", "configured", "configured_main", "configured_missing", "remote"],
)
@pytest.mark.parametrize("workflow_edit", [False, True])
async def test_release_branch_push_excludes_inherited_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, workflow_edit: bool
) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+00:00")
    backend = _GitBackend(tmp_path)
    await backend.git("init -b main")
    await backend.git("config user.email test@example.com")
    await backend.git("config user.name Test")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    inherited = workflows / "inherited.yml"
    inherited.write_text("main workflow\n")
    edited = workflows / "ci.yml"
    edited.write_text("original workflow\n")
    await backend.git("add .")
    await backend.git("commit -qm main")
    await backend.git("update-ref refs/remotes/origin/main HEAD")
    await backend.git("symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main")
    await backend.git("checkout -b v15-stable")
    inherited.write_text("release workflow\n")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-02T00:00:00+00:00")
    await backend.git("commit -qam release")
    release_sha = await backend.git("rev-parse HEAD")
    await backend.git("update-ref refs/remotes/origin/v15-stable HEAD")
    await backend.git("checkout -b feature")
    if base in {"upstream", "configured", "configured_main", "configured_missing"}:
        await backend.git("config remote.origin.url https://github.com/example/repo.git")
        await backend.git("config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'")
        upstream = "v15-stable" if base in {"configured_main", "configured_missing"} else "main"
        await backend.git(f"branch --set-upstream-to=origin/{upstream}")
    elif base == "remote":
        await backend.git("update-ref refs/remotes/origin/feature HEAD")
        await backend.git("config branch.feature.remote .")
        await backend.git("config branch.feature.merge refs/heads/main")
    (tmp_path / "uv.lock").write_text("dependency update\n")
    if workflow_edit:
        edited.write_text("updated workflow\n")
    await backend.git("add .")
    await backend.git("commit -qm update")

    configured_base = {
        "configured": "v15-stable",
        "configured_main": "main",
        "configured_missing": "missing",
        "remote": "main",
    }.get(base)
    request = _Request(f"git -C {shlex.quote(str(tmp_path))} push origin feature")
    monkeypatch.setattr(guard, "_backend", AsyncMock(return_value=backend))
    monkeypatch.setattr(
        guard, "_configurable", lambda request: guard.RunConfig(base_branch=configured_base)
    )
    change = await guard.WorkflowPushGuardMiddleware()._change_for_request(request)

    if not workflow_edit and base not in {"upstream", "configured_main"}:
        assert change is None
        return
    assert change is not None
    expected_files = [".github/workflows/ci.yml"] if workflow_edit else []
    if base in {"upstream", "configured_main"}:
        expected_files.append(".github/workflows/inherited.yml")
        assert change.base_sha == await backend.git("rev-parse main")
    else:
        assert change.base_sha == release_sha
        assert "inherited.yml" not in change.diff_preview
    assert change.files == expected_files
    assert change.diff_stats == {
        "files": len(expected_files),
        "additions": len(expected_files),
        "deletions": len(expected_files),
    }
    assert change.diff_preview == await backend.git(
        f"diff --binary --full-index {shlex.quote(change.base_sha)}..HEAD -- .github/workflows"
    )


class _ApprovalThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, object] = {}

    async def get(self, thread_id: str) -> dict[str, object]:
        return {"metadata": self.metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
        self.metadata.update(metadata)


async def test_release_pushes_share_approval_only_for_identical_workflow_diffs(
    registry_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _GitBackend(tmp_path)
    await backend.git("init -b main")
    await backend.git("config user.email test@example.com")
    await backend.git("config user.name Test")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    inherited = workflows / "inherited.yml"
    inherited.write_text("main workflow\n")
    edited = workflows / "ci.yml"
    edited.write_text("original workflow\n")
    await backend.git("add .")
    await backend.git("commit -qm main")
    await backend.git("init --bare remote.git")
    await backend.git("remote add origin ./remote.git")
    await backend.git("push origin main")
    await backend.git("checkout -b v15-stable")
    inherited.write_text("release workflow\n")
    await backend.git("commit -qam release")
    await backend.git("push origin v15-stable")

    threads = _ApprovalThreads()
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(workflow_approval, "get_client", lambda: client)
    monkeypatch.setattr(guard, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(guard, "_backend", AsyncMock(return_value=backend))
    monkeypatch.setattr(
        guard,
        "_configurable",
        lambda request: guard.RunConfig(thread_id="thread-1", base_branch="v15-stable"),
    )
    notifications: list[str] = []

    async def active(*args: object) -> dict[str, str]:
        return {"channel_id": "C123", "thread_ts": "1700000000.000100"}

    async def post(channel: str, timestamp: str, message: str, **kwargs: object) -> str:
        notifications.append(message)
        return "notification-ts"

    monkeypatch.setattr(guard, "get_active_slack_thread", active)
    monkeypatch.setattr(guard, "post_slack_thread_reply_with_ts", post)
    middleware = guard.WorkflowPushGuardMiddleware()

    async def push(request: ToolCallRequest) -> ToolMessage:
        result = await backend.aexecute(request.tool_call["args"]["command"])
        assert result.exit_code == 0, result.output
        return ToolMessage(content="pushed", tool_call_id="call-1")

    async def attempt(branch: str) -> ToolMessage:
        return cast(
            ToolMessage,
            await middleware.awrap_tool_call(
                cast(
                    ToolCallRequest,
                    _Request(f"git -C {shlex.quote(str(tmp_path))} push origin {branch}"),
                ),
                push,
            ),
        )

    for branch in ["lock-one", "lock-two", "workflow-one", "workflow-two", "different"]:
        await backend.git(f"checkout -b {branch} v15-stable")
        (tmp_path / "uv.lock").write_text(f"{branch}\n")
        if branch.startswith("workflow"):
            edited.write_text("updated workflow\n")
        elif branch == "different":
            edited.write_text("different workflow\n")
        await backend.git("add uv.lock .github/workflows")
        await backend.git(f"commit -qm {branch}")
        result = await attempt(branch)
        if branch.startswith("lock"):
            assert result.content == "pushed"
            assert await workflow_approval.get_workflow_push_approvals("thread-1") == {}
            assert notifications == []
        else:
            assert (
                json.loads(str(result.content))["workflow_approval_status"] == "approval_required"
            )

    approvals = await workflow_approval.get_workflow_push_approvals("thread-1")
    assert len(approvals) == len(notifications) == 2
    shared = next(record for record in approvals.values() if record["branch"] == "workflow-two")
    response = workflow_approval.workflow_push_approval_response(shared)
    assert response["files"] == [".github/workflows/ci.yml"]
    assert "inherited.yml" not in response["diffPreview"]
    monkeypatch.setattr(
        workflow_approval_api,
        "fetch_thread_metadata",
        AsyncMock(return_value={"visibility": "public", "owner_login": "reviewer"}),
    )
    wake = AsyncMock(return_value={"run_id": "approval-run"})
    monkeypatch.setattr(workflow_approval_api, "dispatch_followup", wake)
    await workflow_approval_api.approve_workflow_push(
        "thread-1", shared["fingerprint"], session={"sub": "reviewer"}
    )
    for branch in ["workflow-one", "workflow-two"]:
        await backend.git(f"checkout {branch}")
        assert (await attempt(branch)).content == "pushed"
        assert await backend.git(f"rev-parse refs/remotes/origin/{branch}") == await backend.git(
            "rev-parse HEAD"
        )
    await backend.git("checkout different")
    assert (
        json.loads(str((await attempt("different")).content))["workflow_approval_status"]
        == "approval_required"
    )
    assert len(notifications) == 2
    assert wake.await_count == 1
    assert len(await workflow_approval.get_workflow_push_approvals("thread-1")) == 2

    await backend.git("clone -b v15-stable ./remote.git other")
    other = _GitBackend(tmp_path / "other")
    await other.git("config user.email test@example.com")
    await other.git("config user.name Test")
    await other.git("config remote.origin.url ./remote.git")
    await other.git("checkout -b different")
    (other.root / ".github/workflows/ci.yml").write_text("different workflow\n")
    await other.git("commit -qam different")
    other_change = await guard._workflow_change_for_push(
        other,
        guard.ParsedGitPush(
            repo_dir=str(other.root), remote="origin", local_ref="different", remote_ref="different"
        ),
        base_branch="v15-stable",
    )
    assert other_change is not None
    assert other_change.fingerprint not in approvals


async def test_workflow_change_marks_merge_workflows_as_inherited() -> None:
    change = await guard._workflow_change_for_push(
        _Backend(inherited_from_main=True),
        guard.ParsedGitPush(
            repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
        ),
    )

    assert change is not None
    assert change.inherited_from == "main"


async def test_workflow_change_does_not_misattribute_preexisting_workflows() -> None:
    change = await guard._workflow_change_for_push(
        _Backend(inherited_from_main=True, workflows_changed_by_merge=False),
        guard.ParsedGitPush(
            repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
        ),
    )

    assert change is not None
    assert change.inherited_from is None


def test_workflow_approval_response_serializes_review_fields() -> None:
    from openswe.threads.workflow_approval import workflow_push_approval_response

    response = workflow_push_approval_response(
        {
            "fingerprint": "abc",
            "status": "pending",
            "repo": "https://github.com/langchain-ai/open-swe",
            "branch": "feature",
            "base_sha": "b" * 40,
            "head_sha": "a" * 40,
            "files": [".github/workflows/ci.yml"],
            "diff_stats": {"files": 1, "additions": 2, "deletions": 3},
            "diff_preview": "diff --git ...",
            "diff_preview_truncated": True,
            "inherited_from": "main",
            "approval_url": "https://openswe.vercel.app/agents/thread?workflowApproval=abc",
            "requested_at": "2026-06-30T00:00:00+00:00",
        }
    )

    assert response["fingerprint"] == "abc"
    assert response["baseSha"] == "b" * 40
    assert response["headSha"] == "a" * 40
    assert response["diffStats"] == {"files": 1, "additions": 2, "deletions": 3}
    assert response["diffPreviewTruncated"] is True
    assert response["inheritedFrom"] == "main"
    assert response["approvalUrl"].endswith("workflowApproval=abc")


async def test_workflow_change_for_push_ignores_non_workflow_push() -> None:
    backend = _Backend(workflow_files="")

    assert (
        await guard._workflow_change_for_push(
            backend,
            guard.ParsedGitPush(
                repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
            ),
        )
        is None
    )


async def test_workflow_change_for_push_rejects_non_current_refspec() -> None:
    backend = _Backend()

    with pytest.raises(guard.WorkflowPushInspectionError):
        await guard._workflow_change_for_push(
            backend,
            guard.ParsedGitPush(
                repo_dir="/repo", remote="origin", local_ref="evil", remote_ref="feature"
            ),
        )


@pytest.mark.parametrize("tool_name", ["execute", "background_execute"])
@pytest.mark.parametrize(
    "command",
    [
        "git -C /repo push origin feature",
        "git -C /repo push --force-with-lease origin feature",
        "git -C /repo push origin --force-with-lease feature",
        "git -C /repo push -u origin feature --force-with-lease",
        f"git -C /repo push --force-with-lease=refs/heads/feature:{'b' * 40} origin feature",
        "git -C /repo push --force-with-lease origin HEAD:refs/heads/feature",
        "git -C /repo push --force-with-lease origin refs/heads/feature:refs/heads/feature",
    ],
)
async def test_unapproved_workflow_push_blocks_and_posts_slack(
    monkeypatch: pytest.MonkeyPatch, tool_name: str, command: str
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    guard.SANDBOX_BACKENDS["thread-1"] = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _Backend()), thread_id="thread-1"
    )
    posted: dict[str, Any] = {}

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return False

    pending_kwargs: dict[str, Any] = {}

    async def fake_pending(thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        pending_kwargs.update(kwargs)
        return {"fingerprint": kwargs["fingerprint"], "status": "pending", "notified": False}, True

    async def fake_post(channel_id: str, thread_ts: str, message: str, **kwargs: Any) -> str:
        posted.update(
            channel_id=channel_id, thread_ts=thread_ts, message=message, blocks=kwargs["blocks"]
        )
        return "1700000000.000200"

    async def fake_notified(thread_id: str, fingerprint: str) -> None:
        posted["notified"] = fingerprint

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)
    monkeypatch.setattr(guard, "ensure_workflow_push_pending", fake_pending)
    monkeypatch.setattr(guard, "post_slack_thread_reply_with_ts", fake_post)
    monkeypatch.setattr(guard, "mark_workflow_push_notified", fake_notified)

    class _NoThreadsClient:
        async def get(self, thread_id: str) -> dict[str, str]:
            raise KeyError(thread_id)

    class _NoClient:
        threads = _NoThreadsClient()

    monkeypatch.setattr(guard, "get_client", lambda url: _NoClient())

    called = False

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(command, name=tool_name)), handler
    )

    assert called is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["workflow_approval_status"] == "approval_required"
    assert payload["files"] == [".github/workflows/ci.yml"]
    assert payload["diff_stats"] == {"files": 1, "additions": 1, "deletions": 1}
    assert payload["approval_url"].endswith("?workflowApproval=" + payload["fingerprint"])
    assert pending_kwargs["diff_preview"]
    assert pending_kwargs["diff_preview_truncated"] is False
    assert pending_kwargs["inherited_from"] is None
    assert pending_kwargs["approval_url"] == payload["approval_url"]
    assert posted["channel_id"] == "C123"
    assert "Open in Web" in posted["message"]
    assert "Why confirmation is required" in posted["message"]
    assert posted["blocks"][1]["elements"][0]["text"]["text"] == "Approve & continue push"
    assert posted["blocks"][1]["elements"][1]["text"]["text"] == "Cancel push"
    assert posted["blocks"][1]["elements"][0]["value"]


@pytest.mark.parametrize(
    "lease",
    [
        "",
        "--force-with-lease",
        "--force-with-lease=feature",
        "--force-with-lease=refs/heads/feature:",
        f"--force-with-lease=refs/heads/feature:{'b' * 40}",
    ],
)
@pytest.mark.parametrize(
    "refspec", ["feature", "HEAD:refs/heads/feature", "refs/heads/feature:refs/heads/feature"]
)
async def test_approved_workflow_push_runs_fixed_command(
    monkeypatch: pytest.MonkeyPatch, lease: str, refspec: str
) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _Backend()), thread_id="thread-1"
    )

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return True

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)

    pushed_command = ""

    async def handler(request: Any) -> ToolMessage:
        nonlocal pushed_command
        pushed_command = request.tool_call["args"]["command"]
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(f"git -C /repo push -u origin {refspec} {lease}")),
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "pushed"
    options = f"--set-upstream {lease} ".replace("  ", " ")
    assert pushed_command == (f"git -C /repo push {options}origin {'a' * 40}:refs/heads/feature")


@pytest.mark.parametrize("option", ["", "--force-with-lease", "--verbose"])
@pytest.mark.parametrize("refspec", ["feature", "evil:feature", "feature:other"])
async def test_non_workflow_push_runs_without_approval(
    monkeypatch: pytest.MonkeyPatch, option: str, refspec: str
) -> None:
    command = f"git -C /repo push {option} origin {refspec}"
    guard.SANDBOX_BACKENDS["thread-1"] = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _Backend(workflow_files="")), thread_id="thread-1"
    )
    called = False

    async def fail_approval(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("approval should not be checked")

    monkeypatch.setattr(guard, "workflow_push_approved", fail_approval)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        assert request.tool_call["args"]["command"] == command
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(command)), handler
    )

    assert called is True
    assert isinstance(result, ToolMessage)
    assert result.content == "pushed"


@pytest.mark.parametrize(
    ("refspec", "inspectable"),
    [("evil:feature", True), ("feature:other", True), ("feature", False)],
)
async def test_uninspectable_lease_push_is_blocked(refspec: str, inspectable: bool) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _Backend(inspectable=inspectable)),
        thread_id="thread-1",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise AssertionError("uninspectable lease push must not execute")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(f"git -C /repo push --force-with-lease origin {refspec}")),
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert json.loads(str(result.content))["error_type"] == "WorkflowPushInspectionError"


@pytest.mark.parametrize(
    "command",
    [
        "git -C /repo add -A && git -C /repo commit -qm 'Add CI' && "
        "git -C /repo push -u origin feature",
        "git commit -qam 'Add CI'&&git push",
        "(cd /repo && git push)",
        "cd /repo; git push",
        "git -C /repo push --verbose origin feature:other",
    ],
    ids=["spaced", "unspaced", "subshell", "semicolon", "unsupported-option"],
)
@pytest.mark.parametrize(
    ("backend", "refused"),
    [
        ({"workflow_files": ".github/workflows/ci.yml"}, True),
        ({"workflow_files": ""}, False),
        ({"workflow_files": "<no output>"}, False),
        # Such as a repository the same command clones: nothing shows what it pushes.
        ({"inspectable": False}, True),
    ],
    ids=["workflow-change", "no-workflow-change", "local-shell-no-output", "uninspectable"],
)
async def test_chained_push_runs_only_without_workflow_changes(
    command: str, backend: dict[str, Any], refused: bool
) -> None:
    # The guard can only inspect a push that is the whole command; one chained after
    # a commit would otherwise carry workflow changes past approval.
    guard.SANDBOX_BACKENDS["thread-1"] = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _Backend(**backend)),
        thread_id="thread-1",
    )
    called = False

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request(command)), handler
    )

    assert called is not refused
    assert isinstance(result, ToolMessage)
    if refused:
        assert result.status == "error"
        assert json.loads(str(result.content))["error_type"] == "WorkflowPushMustStandAlone"


@pytest.mark.parametrize(
    ("chained", "connection", "inspectable", "approved", "lease", "error"),
    [
        (True, "missing", True, False, "", "WorkflowPushMustStandAlone"),
        (True, "registered", True, False, "", "WorkflowPushMustStandAlone"),
        (True, "reconnect", True, False, "", "WorkflowPushMustStandAlone"),
        (False, "reconnect", True, False, "", "WorkflowPushApprovalRequired"),
        (False, "registered", True, True, "", None),
        (False, "reconnect", True, True, "", None),
        (False, "missing", True, False, "", "WorkflowPushInspectionError"),
        (False, "missing", True, False, "--force-with-lease ", "WorkflowPushInspectionError"),
        (False, "registered", False, False, "", "WorkflowPushInspectionError"),
        (False, "registered", False, False, "--force-with-lease ", "WorkflowPushInspectionError"),
    ],
)
async def test_check_shell_inspects_workflow_pushes_in_the_threads_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    chained: bool,
    connection: str,
    inspectable: bool,
    approved: bool,
    lease: str,
    error: str | None,
) -> None:
    backend = _Backend(
        inspectable=inspectable,
        pending_workflows=chained,
        workflow_files="" if chained else ".github/workflows/ci.yml",
    )
    if connection == "registered":
        state.set_sandbox_backend("thread-1", cast(SandboxBackendProtocol, backend))

    async def sandbox_id(thread_id: str) -> str:
        assert thread_id == "thread-1"
        if connection == "missing":
            raise RuntimeError("Sandbox metadata unavailable")
        return backend.id

    async def connect_box(
        sandbox_id: str, *, thread_id: str | None = None
    ) -> SandboxBackendProtocol:
        assert sandbox_id == backend.id
        assert thread_id == "thread-1"
        return cast(SandboxBackendProtocol, backend)

    monkeypatch.setattr(state, "get_sandbox_id_from_metadata", sandbox_id)
    monkeypatch.setattr(connect, "connect_sandbox", connect_box)
    monkeypatch.setattr(guard, "workflow_push_approved", AsyncMock(return_value=approved))
    monkeypatch.setattr(
        guard, "ensure_workflow_push_pending", AsyncMock(return_value=({"status": "pending"}, True))
    )
    monkeypatch.setattr(guard, "_post_slack_approval_if_needed", AsyncMock())

    command = f"git -C /repo push {lease}origin feature"
    if chained:
        command = (
            "git -C /repo add .github/workflows/x.yml && git -C /repo commit -m m && " + command
        )
    verdict = await _check_shell(command)

    assert verdict.allowed is (error is None)
    if error is None:
        assert verdict.command == f"git -C /repo push origin {backend.head}:refs/heads/feature"
    else:
        assert json.loads(str(verdict.refusal))["error_type"] == error
    if connection != "missing":
        assert backend.commands
        assert all(
            " push " not in command and " commit " not in command for command in backend.commands
        )


async def _check_shell(command: str) -> ShellVerdict:
    class Parts(RunParts):
        shell_guards = (guard.WorkflowPushGuardMiddleware(),)

    async def never_plan(config: object, surface: ToolSurface) -> None:
        raise AssertionError("Shell checks must not plan a run")

    run = RemoteRun(
        thread_id="thread-1",
        assistant_id="agent",
        configurable={},
        expires_at=int(time.time()) + 3600,
    ).granting()
    verdict = await RemoteGraph("agent", never_plan, Parts()).api(
        run, "check_shell", {"run_id": "run-1", "tool": "execute", "command": command}
    )
    return ShellVerdict.model_validate(verdict)
