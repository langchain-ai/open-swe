"""Gate workflow-file pushes on human approval."""

import hashlib
import json
import logging
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langgraph_sdk import get_client

from openswe.middleware.git_push import (
    ParsedGitPush,
    PushInspectionError,
    chained_push_dirs,
    first_line,
    normalize_remote,
    parse_git_push,
    push_range,
    run_git,
)
from openswe.middleware.pr_creation_guard import shell_commands
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.slack.client import (
    LANGGRAPH_URL,
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
)
from openswe.slack.tools.reply import build_workflow_approval_blocks
from openswe.threads.workflow_approval import (
    ensure_workflow_push_pending,
    mark_workflow_push_notified,
    workflow_push_approved,
)
from openswe.utils.dashboard_links import dashboard_workflow_approval_url

logger = logging.getLogger(__name__)

_WORKFLOW_PREFIX = ".github/workflows/"
_DIFF_PREVIEW_MAX_CHARS = 20_000
_DIFF_PREVIEW_MAX_LINES = 400


@dataclass(frozen=True)
class WorkflowPushChange:
    fingerprint: str
    repo: str
    branch: str
    base_sha: str
    head_sha: str
    files: list[str]
    diff_stats: dict[str, int]
    diff_preview: str
    diff_preview_truncated: bool
    inherited_from: str | None
    remote: str
    local_ref: str
    remote_ref: str
    fixed_command: str


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    tool_call = getattr(request, "tool_call", None)
    args = tool_call.get("args") if isinstance(tool_call, Mapping) else None
    return dict(args) if isinstance(args, Mapping) else {}


def _tool_call_id(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        value = tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _config(request: ToolCallRequest) -> Mapping[str, Any]:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(runtime_config, Mapping):
        return runtime_config
    try:
        config = get_config()
    except Exception:
        return {}
    return config if isinstance(config, Mapping) else {}


def _configurable(request: ToolCallRequest) -> RunConfig:
    return RunConfig.from_config(_config(request))


def _thread_id(request: ToolCallRequest) -> str | None:
    return _configurable(request).thread_id or None


def _backend(thread_id: str | None) -> Any | None:
    return SANDBOX_BACKENDS.get(thread_id) if thread_id else None


async def _may_push_workflow_changes(backend: Any, repo_dir: str | None) -> bool:
    """Whether a push from ``repo_dir`` could carry ``.github/workflows`` changes.

    Pending edits count: a chained ``git commit`` may commit them before the push.
    A repository that cannot be inspected counts too, such as one the same
    command clones: nothing shows what it would push.
    """
    pending = await run_git(
        backend, repo_dir, "status --porcelain --untracked-files=all -- .github/workflows"
    )
    if not pending.ok or pending.output:
        return True
    for base in ("@{upstream}", "origin/HEAD"):
        unpushed = await run_git(
            backend,
            repo_dir,
            f"diff --name-only {shlex.quote(f'{base}...HEAD')} -- .github/workflows",
        )
        if unpushed.ok:
            return bool(unpushed.output)
    return True


def _chained_push_message(request: ToolCallRequest) -> ToolMessage:
    content = {
        "status": "error",
        "error_type": "WorkflowPushMustStandAlone",
        "error": (
            "This command pushes as part of a longer command, and that push could carry "
            "GitHub workflow file changes, so Open SWE cannot show the thread owner what it "
            "pushes. Nothing ran. Commit first, then run the push on its own (for example "
            "`git -C <repo> push -u origin <branch>`); workflow changes then go to the "
            "thread owner for approval."
        ),
    }
    return ToolMessage(
        content=json.dumps(content), tool_call_id=_tool_call_id(request) or "", status="error"
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _diff_preview(diff: str) -> tuple[str, bool]:
    if len(diff) <= _DIFF_PREVIEW_MAX_CHARS:
        lines = diff.splitlines()
        if len(lines) <= _DIFF_PREVIEW_MAX_LINES:
            return diff, False
    preview_lines: list[str] = []
    char_count = 0
    truncated = False
    for line in diff.splitlines():
        next_count = char_count + len(line) + 1
        if len(preview_lines) >= _DIFF_PREVIEW_MAX_LINES or next_count > _DIFF_PREVIEW_MAX_CHARS:
            truncated = True
            break
        preview_lines.append(line)
        char_count = next_count
    return "\n".join(preview_lines), truncated


def _diff_stats(files: list[str], numstat: str) -> dict[str, int]:
    additions = 0
    deletions = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if parts[0].isdigit():
            additions += int(parts[0])
        if parts[1].isdigit():
            deletions += int(parts[1])
    return {"files": len(files), "additions": additions, "deletions": deletions}


async def _inherited_workflow_source(backend: Any, root: str, head: str) -> str | None:
    parents = await run_git(backend, root, f"rev-list --parents -n 1 {shlex.quote(head)}")
    parts = first_line(parents.output).split() if parents.ok else []
    if len(parts) != 3:
        return None
    first_parent, merged_parent = parts[1:]
    if first_parent == merged_parent:
        return None
    origin_head = await run_git(backend, root, "symbolic-ref --short refs/remotes/origin/HEAD")
    source_ref = first_line(origin_head.output) if origin_head.ok else ""
    if not source_ref:
        return None
    source_contains_parent = await run_git(
        backend,
        root,
        f"merge-base --is-ancestor {shlex.quote(merged_parent)} {shlex.quote(source_ref)}",
    )
    if not source_contains_parent.ok:
        return None
    feature_workflow_diff = await run_git(
        backend,
        root,
        f"diff --quiet {shlex.quote(first_parent)} {shlex.quote(head)} -- .github/workflows",
    )
    if feature_workflow_diff.ok:
        return None
    merged_workflow_diff = await run_git(
        backend,
        root,
        f"diff --quiet {shlex.quote(merged_parent)} {shlex.quote(head)} -- .github/workflows",
    )
    return source_ref.removeprefix("origin/") if merged_workflow_diff.ok else None


def _approval_url(thread_id: str | None, fingerprint: str) -> str | None:
    if not thread_id:
        return None
    return dashboard_workflow_approval_url(thread_id, fingerprint)


async def _workflow_change_for_push(
    backend: Any, parsed: ParsedGitPush
) -> WorkflowPushChange | None:
    pushed = await push_range(backend, parsed)
    if pushed is None:
        return None
    root, branch_name, head = pushed.root, pushed.branch, pushed.head
    base_sha, range_expr = pushed.base_sha, pushed.range_expr

    names = await run_git(
        backend,
        root,
        f"diff --name-only --diff-filter=ACMRTD {range_expr} -- .github/workflows",
    )
    if not names.ok:
        return parsed.inspection_failed()
    files = sorted(
        line.strip()
        for line in names.output.splitlines()
        if line.strip().startswith(_WORKFLOW_PREFIX)
    )
    if not files:
        return None

    diff = await run_git(
        backend, root, f"diff --binary --full-index {range_expr} -- .github/workflows"
    )
    if not diff.ok or not diff.output:
        return parsed.inspection_failed()
    numstat = await run_git(backend, root, f"diff --numstat {range_expr} -- .github/workflows")
    diff_preview, diff_preview_truncated = _diff_preview(diff.output)
    diff_stats = _diff_stats(files, numstat.output if numstat.ok else "")
    inherited_from = await _inherited_workflow_source(backend, root, head)

    remote = await run_git(backend, root, "config --get remote.origin.url")
    repo = normalize_remote(first_line(remote.output)) if remote.ok else ""
    fixed_refspec = f"{head}:refs/heads/{parsed.remote_branch}"
    fixed_command = parsed.command_for_head(root, head)
    payload = {
        "repo": repo,
        "branch": branch_name,
        "base_sha": base_sha,
        "head_sha": head,
        "files": files,
        "diff": diff.output,
        "remote": parsed.remote,
        "local_ref": parsed.local_ref,
        "remote_ref": parsed.remote_ref,
        "fixed_refspec": fixed_refspec,
        "force_with_lease": parsed.force_with_lease,
    }
    return WorkflowPushChange(
        fingerprint=_fingerprint(payload),
        repo=repo,
        branch=branch_name,
        base_sha=base_sha,
        head_sha=head,
        files=files,
        diff_stats=diff_stats,
        diff_preview=diff_preview,
        diff_preview_truncated=diff_preview_truncated,
        inherited_from=inherited_from,
        remote=parsed.remote,
        local_ref=parsed.local_ref,
        remote_ref=parsed.remote_ref,
        fixed_command=fixed_command,
    )


def _blocked_message(
    change: WorkflowPushChange,
    *,
    approval_url: str | None = None,
    already_rejected: bool = False,
) -> ToolMessage:
    status = "rejected" if already_rejected else "approval_required"
    content = {
        "status": "error",
        "error_type": "WorkflowPushApprovalRequired",
        "error": (
            "This git push includes GitHub workflow file changes and requires human "
            "approval before Open SWE can push it. Retry the same standalone git push "
            "after the thread owner approves the workflow diff in Slack or the web UI."
        ),
        "workflow_approval_status": status,
        "fingerprint": change.fingerprint,
        "files": change.files,
        "repo": change.repo,
        "branch": change.branch,
        "base_sha": change.base_sha,
        "head_sha": change.head_sha,
        "diff_stats": change.diff_stats,
        "diff_preview_truncated": change.diff_preview_truncated,
        "approval_url": approval_url,
    }
    return ToolMessage(content=json.dumps(content), tool_call_id="", status="error")


def _tool_message_for_request(message: ToolMessage, request: ToolCallRequest) -> ToolMessage:
    return message.model_copy(update={"tool_call_id": _tool_call_id(request)})


def _override_execute_command(request: ToolCallRequest, command: str) -> ToolCallRequest:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, Mapping):
        return request
    args = dict(_tool_args(request))
    args["command"] = command
    return request.override(tool_call=cast(ToolCall, {**dict(tool_call), "args": args}))


def _approval_slack_message(change: WorkflowPushChange, approval_url: str | None = None) -> str:
    files = "\n".join(f"• `{path}`" for path in change.files[:10])
    if len(change.files) > 10:
        files += f"\n• …and {len(change.files) - 10} more"
    repo = change.repo or "the repository"
    branch = change.branch or "the current branch"
    stats = change.diff_stats
    web_review = f"\n\n*Review diff:* <{approval_url}|Open in Web>" if approval_url else ""
    if change.inherited_from:
        headline = "*Confirm workflow changes inherited from the base branch*"
        source = (
            f"This branch now includes {len(change.files)} GitHub Actions files from merging "
            f"`{change.inherited_from}`. Open SWE did not author these workflow changes."
        )
    else:
        headline = "*Confirm GitHub Actions workflow changes*"
        source = f"Open SWE is ready to push workflow changes in `{repo}` on `{branch}`."
    return (
        f"{headline}\n{source}\n\n"
        "*Why confirmation is required:* Workflow files control CI jobs and may access "
        "repository secrets.\n\n"
        f"*Files:*\n{files}\n\n"
        f"*Diff stat:* {stats.get('files', len(change.files))} files, "
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}\n"
        f"*Approval ID:* `{change.fingerprint}`{web_review}\n\n"
        "Approval resumes this exact push only. If the workflow files change, Open SWE will "
        "ask again."
    )


async def _post_slack_approval_if_needed(
    request: ToolCallRequest, change: WorkflowPushChange, record: Mapping[str, Any]
) -> None:
    if record.get("notified") is True:
        return
    cfg = _configurable(request)
    active = await get_active_slack_thread(
        get_client(url=LANGGRAPH_URL),
        cfg.thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    if not active:
        return
    channel_id = active.get("channel_id")
    thread_ts = active.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return
    message = _approval_slack_message(
        change, _approval_url(_thread_id(request), change.fingerprint)
    )
    await post_slack_thread_reply_with_ts(
        channel_id,
        thread_ts,
        message,
        blocks=build_workflow_approval_blocks(message, change.fingerprint),
        agent_thread_id=cfg.thread_id,
    )
    thread_id = _thread_id(request)
    if thread_id:
        await mark_workflow_push_notified(thread_id, change.fingerprint)


async def _approval_state(request: ToolCallRequest, change: WorkflowPushChange) -> str:
    thread_id = _thread_id(request)
    if not thread_id:
        return "missing_thread"
    try:
        approval_url = _approval_url(thread_id, change.fingerprint)
        if await workflow_push_approved(thread_id, change.fingerprint):
            return "approved"
        record, _created = await ensure_workflow_push_pending(
            thread_id,
            fingerprint=change.fingerprint,
            repo=change.repo,
            branch=change.branch,
            base_sha=change.base_sha,
            head_sha=change.head_sha,
            files=change.files,
            diff_stats=change.diff_stats,
            diff_preview=change.diff_preview,
            diff_preview_truncated=change.diff_preview_truncated,
            inherited_from=change.inherited_from,
            approval_url=approval_url,
        )
        await _post_slack_approval_if_needed(request, change, record)
        return str(record.get("status") or "pending")
    except Exception:
        logger.exception("Failed to read or write workflow push approval state")
        return "approval_error"


class WorkflowPushGuardMiddleware(OpenSWEMiddleware):
    """Require approval before pushing `.github/workflows` changes."""

    state_schema = AgentState

    async def _change_for_request(self, request: ToolCallRequest) -> WorkflowPushChange | None:
        for command in shell_commands(request):
            parsed = parse_git_push(command)
            if parsed is None:
                continue
            backend = _backend(_thread_id(request))
            if backend is None:
                return parsed.inspection_failed()
            return await _workflow_change_for_push(backend, parsed)
        return None

    async def _handle_change_async(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        change: WorkflowPushChange,
    ) -> ToolMessage | Command:
        thread_id = _thread_id(request)
        state = await _approval_state(request, change)
        if state == "approved":
            safe_request = _override_execute_command(request, change.fixed_command)
            return await handler(safe_request)
        return _tool_message_for_request(
            _blocked_message(
                change,
                approval_url=_approval_url(thread_id, change.fingerprint),
                already_rejected=state == "rejected",
            ),
            request,
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            change = await self._change_for_request(request)
        except PushInspectionError as exc:
            return ToolMessage(
                content=json.dumps(
                    {
                        "status": "error",
                        "error_type": "WorkflowPushInspectionError",
                        "error": str(exc),
                    }
                ),
                tool_call_id=_tool_call_id(request) or "",
                status="error",
            )
        if change is not None:
            return await self._handle_change_async(request, handler, change)
        if await self._chained_workflow_push(request):
            return _chained_push_message(request)
        return await handler(request)

    async def _chained_workflow_push(self, request: ToolCallRequest) -> bool:
        """Whether ``request`` pushes workflow changes as one step of a longer command."""
        dirs = [
            repo_dir
            for command in shell_commands(request)
            for repo_dir in chained_push_dirs(command)
        ]
        backend = _backend(_thread_id(request)) if dirs else None
        if backend is None:
            return False
        for repo_dir in dirs:
            if await _may_push_workflow_changes(backend, repo_dir):
                return True
        return False
