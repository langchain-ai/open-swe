import json
import shlex
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import LocalShellBackend
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
        if any(f"rev-parse {ref}" in command for ref in ("HEAD", "feature", "refs/heads/feature")):
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


@pytest.mark.parametrize(
    "command",
    ["git -C /repo push origin feature", "git -C /repo push --force-with-lease origin feature"],
)
async def test_non_workflow_push_runs_without_approval(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
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
        cast(SandboxBackendProtocol, _Backend(workflow_files="", inspectable=inspectable)),
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
    ],
    ids=["spaced", "unspaced", "subshell", "semicolon"],
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


async def test_check_shell_gates_real_workflow_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = LocalShellBackend(root_dir=tmp_path)

    async def execute(command: str) -> str:
        result = await backend.aexecute(command)
        assert result.exit_code == 0, result.output
        return "" if result.output.strip() == "<no output>" else result.output.strip()

    await execute(
        "git init --bare remote.git && git init -b main repo && "
        "git -C repo config user.name Test && git -C repo config user.email test@example.com && "
        "git -C repo commit --allow-empty -m initial && "
        "git -C repo remote add origin ../remote.git && git -C repo push -u origin main && "
        "git -C repo remote set-head origin main && git -C repo switch -c feature"
    )
    initial = await execute("git -C repo rev-parse HEAD")
    workflow = tmp_path / "repo/.github/workflows/x.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: test\non: push\njobs: {}\n")

    monkeypatch.setattr(state, "get_sandbox_id_from_metadata", AsyncMock(return_value=backend.id))

    async def connect_box(
        sandbox_id: str, *, thread_id: str | None = None
    ) -> SandboxBackendProtocol:
        assert sandbox_id == backend.id
        assert thread_id == "thread-1"
        return backend

    monkeypatch.setattr(connect, "connect_sandbox", connect_box)
    approved = AsyncMock(return_value=False)
    monkeypatch.setattr(guard, "workflow_push_approved", approved)
    monkeypatch.setattr(
        guard, "ensure_workflow_push_pending", AsyncMock(return_value=({"status": "pending"}, True))
    )
    monkeypatch.setattr(guard, "_post_slack_approval_if_needed", AsyncMock())

    async def screened(command: str) -> ShellVerdict:
        verdict = await _check_shell(command)
        if verdict.allowed:
            await execute(verdict.command or command)
        return verdict

    chained = (
        "git -C repo add .github/workflows/x.yml && git -C repo commit -m workflow && "
        "git -C repo push origin feature"
    )
    verdict = await screened(chained)
    assert not verdict.allowed
    assert json.loads(str(verdict.refusal))["error_type"] == "WorkflowPushMustStandAlone"
    assert await execute("git -C repo rev-parse HEAD") == initial
    assert await execute("git -C repo diff --cached --name-only") == ""
    assert await execute("git --git-dir=remote.git for-each-ref refs/heads/feature") == ""

    await execute("git -C repo add .github/workflows/x.yml && git -C repo commit -m workflow")
    head = await execute("git -C repo rev-parse HEAD")
    state.SANDBOX_BACKENDS.clear()
    verdict = await screened("git -C repo push origin feature")
    assert not verdict.allowed
    assert json.loads(str(verdict.refusal))["error_type"] == "WorkflowPushApprovalRequired"
    assert await execute("git --git-dir=remote.git for-each-ref refs/heads/feature") == ""
    fingerprint = approved.call_args.args[1]

    state.SANDBOX_BACKENDS.clear()
    approved.return_value = True
    verdict = await screened("git -C repo push origin feature")
    assert verdict.allowed
    root = shlex.quote(str(tmp_path / "repo"))
    assert verdict.command == f"git -C {root} push origin {head}:refs/heads/feature"
    assert approved.call_args.args == ("thread-1", fingerprint)
    assert await execute("git --git-dir=remote.git rev-parse refs/heads/feature") == head
