"""Gate workflow-file pushes on human approval."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ..dashboard.workflow_approval import (
    ensure_workflow_push_pending,
    mark_workflow_push_notified,
    workflow_push_approved,
)
from ..tools.slack_thread_reply import build_workflow_approval_blocks
from ..utils.dashboard_links import dashboard_workflow_approval_url
from ..utils.github_app import (
    BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    RUNTIME_PROXY_TOKEN_PERMISSIONS,
    WORKFLOW_RUNTIME_PROXY_TOKEN_PERMISSIONS,
)
from ..utils.github_proxy import refresh_proxy_token
from ..utils.sandbox_state import SANDBOX_BACKENDS
from ..utils.slack import post_slack_thread_reply_with_ts

logger = logging.getLogger(__name__)

_WORKFLOW_PREFIX = ".github/workflows/"
_SHELL_OPERATORS = {";", "|", "||", "&"}
_REF_NAME = re.compile(r"^[A-Za-z0-9._/@+-]+$")
_GIT_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_UNSAFE_RAW_COMMAND = re.compile(r"[;|`$<>\n\r]")
_DIFF_PREVIEW_MAX_CHARS = 20_000
_DIFF_PREVIEW_MAX_LINES = 400


@dataclass(frozen=True)
class ParsedGitPush:
    repo_dir: str | None
    remote: str
    local_ref: str
    remote_ref: str
    set_upstream: bool = False


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
    remote: str
    local_ref: str
    remote_ref: str
    fixed_command: str


@dataclass(frozen=True)
class GitInspectResult:
    output: str
    ok: bool


def _tool_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        name = tool_call.get("name")
        return name if isinstance(name, str) else None
    return None


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


def _configurable(request: ToolCallRequest) -> Mapping[str, Any]:
    config = _config(request)
    configurable = config.get("configurable")
    return configurable if isinstance(configurable, Mapping) else {}


def _thread_id(request: ToolCallRequest) -> str | None:
    thread_id = _configurable(request).get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _backend(thread_id: str | None) -> Any | None:
    return SANDBOX_BACKENDS.get(thread_id) if thread_id else None


def _response_output(response: Any) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(response, Mapping):
        value = response.get("output")
        if isinstance(value, str):
            return value
    return str(response or "")


def _response_ok(response: Any) -> bool:
    exit_code = getattr(response, "exit_code", None)
    if isinstance(exit_code, int):
        return exit_code == 0
    if isinstance(response, Mapping):
        value = response.get("exit_code")
        if isinstance(value, int):
            return value == 0
    return True


def _parse_git_push(command: str) -> ParsedGitPush | None:
    stripped = command.strip()
    if _UNSAFE_RAW_COMMAND.search(stripped) or "&" in stripped.replace("&&", ""):
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if not tokens:
        return None

    if len(tokens) >= 4 and tokens[0] == "cd" and tokens[2] == "&&":
        if any(token in _SHELL_OPERATORS or token == "&&" for token in tokens[3:]):
            return None
        return _parse_git_tokens(tokens[3:], repo_dir=tokens[1])

    if any(token in _SHELL_OPERATORS or token == "&&" for token in tokens):
        return None
    return _parse_git_tokens(tokens, repo_dir=None)


def _parse_git_tokens(tokens: list[str], *, repo_dir: str | None) -> ParsedGitPush | None:
    if not tokens or tokens[0] != "git":
        return None
    i = 1
    while i < len(tokens) and tokens[i] != "push":
        if tokens[i] == "-C" and i + 1 < len(tokens):
            repo_dir = tokens[i + 1]
            i += 2
            continue
        return None
    if i >= len(tokens) or tokens[i] != "push":
        return None
    return _parse_push_args(tokens[i + 1 :], repo_dir=repo_dir)


def _parse_push_args(tokens: list[str], *, repo_dir: str | None) -> ParsedGitPush | None:
    set_upstream = False
    while tokens and tokens[0] in {"-u", "--set-upstream"}:
        set_upstream = True
        tokens = tokens[1:]
    if len(tokens) != 2 or tokens[0] != "origin":
        return None
    parsed = _parse_refspec(tokens[1])
    if parsed is None:
        return None
    local_ref, remote_ref = parsed
    return ParsedGitPush(
        repo_dir=repo_dir,
        remote="origin",
        local_ref=local_ref,
        remote_ref=remote_ref,
        set_upstream=set_upstream,
    )


def _parse_refspec(refspec: str) -> tuple[str, str] | None:
    if refspec.startswith("-") or ".." in refspec:
        return None
    if ":" in refspec:
        parts = refspec.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        local_ref, remote_ref = parts
    else:
        local_ref = remote_ref = refspec
    if not _safe_ref(local_ref, allow_head=True) or not _safe_ref(remote_ref, allow_head=False):
        return None
    return local_ref, remote_ref


def _safe_ref(ref: str, *, allow_head: bool) -> bool:
    if allow_head and ref == "HEAD":
        return True
    if ref == "HEAD" or not _REF_NAME.fullmatch(ref):
        return False
    return not any(part in {"", ".", ".."} for part in ref.split("/"))


def _git_command(repo_dir: str | None, args: str) -> str:
    if repo_dir:
        return f"git -C {shlex.quote(repo_dir)} {args}"
    return f"git {args}"


def _run_git(backend: Any, repo_dir: str | None, args: str) -> GitInspectResult:
    try:
        response = backend.execute(_git_command(repo_dir, args), timeout=30)
    except Exception:
        logger.debug("workflow push inspection failed for git %s", args, exc_info=True)
        return GitInspectResult("", False)
    return GitInspectResult(_response_output(response).strip(), _response_ok(response))


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _normalize_remote(remote: str) -> str:
    value = remote.strip()
    if value.endswith(".git"):
        value = value[:-4]
    value = re.sub(r"^https://[^/@]+@github\.com/", "https://github.com/", value)
    value = re.sub(r"^git@github\.com:", "https://github.com/", value)
    return value


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


def _approval_url(thread_id: str | None, fingerprint: str) -> str | None:
    if not thread_id:
        return None
    return dashboard_workflow_approval_url(thread_id, fingerprint)


def _workflow_change_for_push(backend: Any, parsed: ParsedGitPush) -> WorkflowPushChange | None:
    root_result = _run_git(backend, parsed.repo_dir, "rev-parse --show-toplevel")
    if not root_result.ok:
        return None
    root = _first_line(root_result.output)
    if not root:
        return None

    branch = _run_git(backend, root, "rev-parse --abbrev-ref HEAD")
    branch_name = _first_line(branch.output) if branch.ok else ""
    if not branch_name or branch_name == "HEAD" or parsed.remote_ref != branch_name:
        return None
    if parsed.local_ref not in {"HEAD", branch_name}:
        return None

    target_sha = _run_git(backend, root, f"rev-parse {shlex.quote(parsed.local_ref)}")
    head = _first_line(target_sha.output) if target_sha.ok else ""
    if not head or not _GIT_OBJECT_ID.fullmatch(head):
        return None

    remote_branch = f"refs/remotes/{parsed.remote}/{parsed.remote_ref}"
    remote_branch_exists = _run_git(
        backend, root, f"rev-parse --verify {shlex.quote(remote_branch)}"
    )
    if remote_branch_exists.ok and _first_line(remote_branch_exists.output):
        base_ref = remote_branch
        range_expr = f"{shlex.quote(base_ref)}..{shlex.quote(head)}"
        base_sha = _first_line(_run_git(backend, root, f"rev-parse {shlex.quote(base_ref)}").output)
    else:
        origin_head = _run_git(backend, root, "symbolic-ref --short refs/remotes/origin/HEAD")
        base_ref = _first_line(origin_head.output) if origin_head.ok else "origin/main"
        range_expr = f"{shlex.quote(base_ref)}...{shlex.quote(head)}"
        base_sha = _first_line(
            _run_git(
                backend, root, f"merge-base {shlex.quote(head)} {shlex.quote(base_ref)}"
            ).output
        )

    names = _run_git(
        backend,
        root,
        f"diff --name-only --diff-filter=ACMRTD {range_expr} -- .github/workflows",
    )
    if not names.ok:
        return None
    files = sorted(
        line.strip()
        for line in names.output.splitlines()
        if line.strip().startswith(_WORKFLOW_PREFIX)
    )
    if not files:
        return None

    diff = _run_git(backend, root, f"diff --binary --full-index {range_expr} -- .github/workflows")
    if not diff.ok or not diff.output:
        return None
    numstat = _run_git(backend, root, f"diff --numstat {range_expr} -- .github/workflows")
    diff_preview, diff_preview_truncated = _diff_preview(diff.output)
    diff_stats = _diff_stats(files, numstat.output if numstat.ok else "")

    remote = _run_git(backend, root, "config --get remote.origin.url")
    repo = _normalize_remote(_first_line(remote.output)) if remote.ok else ""
    fixed_refspec = f"{head}:refs/heads/{parsed.remote_ref}"
    fixed_args = ["push"]
    if parsed.set_upstream:
        fixed_args.append("--set-upstream")
    fixed_args.extend([parsed.remote, fixed_refspec])
    fixed_command = _git_command(root, " ".join(shlex.quote(arg) for arg in fixed_args))
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
    message.tool_call_id = _tool_call_id(request)
    return message


def _override_execute_command(request: ToolCallRequest, command: str) -> ToolCallRequest:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, Mapping):
        return request
    args = dict(_tool_args(request))
    args["command"] = command
    return request.override(tool_call={**dict(tool_call), "args": args})


def _approval_slack_message(change: WorkflowPushChange, approval_url: str | None = None) -> str:
    files = "\n".join(f"• `{path}`" for path in change.files[:10])
    if len(change.files) > 10:
        files += f"\n• …and {len(change.files) - 10} more"
    repo = change.repo or "the repository"
    branch = change.branch or "the current branch"
    stats = change.diff_stats
    web_review = f"\n\n*Review diff:* <{approval_url}|Open in Web>" if approval_url else ""
    return (
        "*Workflow file approval required*\n"
        f"Open SWE is trying to push changes to GitHub workflow files in `{repo}` on `{branch}`.\n\n"
        f"*Files:*\n{files}\n\n"
        f"*Diff stat:* {stats.get('files', len(change.files))} files, "
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}\n"
        f"*Fingerprint:* `{change.fingerprint}`{web_review}\n\n"
        "Approve only if this exact workflow diff is expected. If the workflow files change, "
        "a new fingerprint will be required."
    )


async def _post_slack_approval_if_needed(
    request: ToolCallRequest, change: WorkflowPushChange, record: Mapping[str, Any]
) -> None:
    if record.get("notified") is True:
        return
    configurable = _configurable(request)
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return
    message = _approval_slack_message(
        change, _approval_url(_thread_id(request), change.fingerprint)
    )
    message_ts, error = await post_slack_thread_reply_with_ts(
        channel_id,
        thread_ts,
        message,
        blocks=build_workflow_approval_blocks(message, change.fingerprint),
    )
    if message_ts and not error:
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
            approval_url=approval_url,
        )
        await _post_slack_approval_if_needed(request, change, record)
        return str(record.get("status") or "pending")
    except Exception:
        logger.exception("Failed to read or write workflow push approval state")
        return "approval_error"


async def _run_with_workflow_token(
    thread_id: str,
    run: Callable[[], Awaitable[ToolMessage | Command]],
) -> ToolMessage | Command:
    elevated = await refresh_proxy_token(
        thread_id, permissions=WORKFLOW_RUNTIME_PROXY_TOKEN_PERMISSIONS
    )
    try:
        return await run()
    finally:
        if elevated:
            restored = await refresh_proxy_token(
                thread_id, permissions=RUNTIME_PROXY_TOKEN_PERMISSIONS
            )
            if not restored:
                await refresh_proxy_token(
                    thread_id, permissions=BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS
                )


class WorkflowPushGuardMiddleware(AgentMiddleware):
    """Require approval before pushing `.github/workflows` changes."""

    state_schema = AgentState

    def _change_for_request(self, request: ToolCallRequest) -> WorkflowPushChange | None:
        if _tool_name(request) != "execute":
            return None
        command = _tool_args(request).get("command")
        if not isinstance(command, str):
            return None
        parsed = _parse_git_push(command)
        if parsed is None:
            return None
        backend = _backend(_thread_id(request))
        if backend is None:
            return None
        return _workflow_change_for_push(backend, parsed)

    async def _handle_change_async(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
        change: WorkflowPushChange,
    ) -> ToolMessage | Command:
        thread_id = _thread_id(request)
        state = await _approval_state(request, change)
        if state == "approved" and thread_id:
            safe_request = _override_execute_command(request, change.fixed_command)
            return await _run_with_workflow_token(thread_id, lambda: handler(safe_request))
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
        change = self._change_for_request(request)
        if change is None:
            return await handler(request)
        return await self._handle_change_async(request, handler, change)
