"""Gate workflow-file pushes on human approval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import threading
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
from ..utils.github_app import (
    RUNTIME_PROXY_TOKEN_PERMISSIONS,
    WORKFLOW_RUNTIME_PROXY_TOKEN_PERMISSIONS,
)
from ..utils.github_proxy import refresh_proxy_token
from ..utils.sandbox_state import SANDBOX_BACKENDS
from ..utils.slack import post_slack_thread_reply_with_ts

logger = logging.getLogger(__name__)

_WORKFLOW_PREFIX = ".github/workflows/"
_SHELL_OPERATORS = {";", "|", "||", "&"}
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*")


@dataclass(frozen=True)
class ParsedGitPush:
    repo_dir: str | None


@dataclass(frozen=True)
class WorkflowPushChange:
    fingerprint: str
    repo: str
    branch: str
    base_sha: str
    head_sha: str
    files: list[str]


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
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        return None
    if not tokens:
        return None
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
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
    while i < len(tokens):
        token = tokens[i]
        if token == "push":
            return ParsedGitPush(repo_dir=repo_dir)
        if token == "-C" and i + 1 < len(tokens):
            repo_dir = tokens[i + 1]
            i += 2
            continue
        i += 1
    return None


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


def _run_coroutine_sync(coro: Awaitable[ToolMessage | Command]) -> ToolMessage | Command:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, ToolMessage | Command | BaseException] = {}

    def target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["value"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    value = result["value"]
    if isinstance(value, BaseException):
        raise value
    return value


def _workflow_change_for_push(backend: Any, parsed: ParsedGitPush) -> WorkflowPushChange | None:
    root_result = _run_git(backend, parsed.repo_dir, "rev-parse --show-toplevel")
    if not root_result.ok:
        return None
    root = _first_line(root_result.output)
    if not root:
        return None

    upstream = _run_git(backend, root, "rev-parse --abbrev-ref --symbolic-full-name @{u}")
    if upstream.ok and _first_line(upstream.output):
        base_ref = _first_line(upstream.output)
        range_expr = f"{shlex.quote(base_ref)}..HEAD"
        base_sha = _first_line(_run_git(backend, root, f"rev-parse {shlex.quote(base_ref)}").output)
    else:
        origin_head = _run_git(backend, root, "symbolic-ref --short refs/remotes/origin/HEAD")
        base_ref = _first_line(origin_head.output) if origin_head.ok else "origin/main"
        range_expr = f"{shlex.quote(base_ref)}...HEAD"
        base_sha = _first_line(
            _run_git(backend, root, f"merge-base HEAD {shlex.quote(base_ref)}").output
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

    remote = _run_git(backend, root, "config --get remote.origin.url")
    branch = _run_git(backend, root, "rev-parse --abbrev-ref HEAD")
    head_sha = _run_git(backend, root, "rev-parse HEAD")
    repo = _normalize_remote(_first_line(remote.output)) if remote.ok else ""
    branch_name = _first_line(branch.output) if branch.ok else ""
    head = _first_line(head_sha.output) if head_sha.ok else ""
    payload = {
        "repo": repo,
        "branch": branch_name,
        "base_sha": base_sha,
        "head_sha": head,
        "files": files,
        "diff": diff.output,
    }
    return WorkflowPushChange(
        fingerprint=_fingerprint(payload),
        repo=repo,
        branch=branch_name,
        base_sha=base_sha,
        head_sha=head,
        files=files,
    )


def _blocked_message(change: WorkflowPushChange, *, already_rejected: bool = False) -> ToolMessage:
    status = "rejected" if already_rejected else "approval_required"
    content = {
        "status": "error",
        "error_type": "WorkflowPushApprovalRequired",
        "error": (
            "This git push includes GitHub workflow file changes and requires human "
            "approval before Open SWE can push it. Retry the same standalone git push "
            "after the thread owner approves the workflow diff."
        ),
        "workflow_approval_status": status,
        "fingerprint": change.fingerprint,
        "files": change.files,
        "repo": change.repo,
        "branch": change.branch,
    }
    return ToolMessage(content=json.dumps(content), tool_call_id="", status="error")


def _tool_message_for_request(message: ToolMessage, request: ToolCallRequest) -> ToolMessage:
    message.tool_call_id = _tool_call_id(request)
    return message


def _approval_slack_message(change: WorkflowPushChange) -> str:
    files = "\n".join(f"• `{path}`" for path in change.files[:10])
    if len(change.files) > 10:
        files += f"\n• …and {len(change.files) - 10} more"
    repo = change.repo or "the repository"
    branch = change.branch or "the current branch"
    return (
        "*Workflow file approval required*\n"
        f"Open SWE is trying to push changes to GitHub workflow files in `{repo}` on `{branch}`.\n\n"
        f"*Files:*\n{files}\n\n"
        f"*Fingerprint:* `{change.fingerprint}`\n\n"
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
    message = _approval_slack_message(change)
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
            await refresh_proxy_token(thread_id, permissions=RUNTIME_PROXY_TOKEN_PERMISSIONS)


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
            return await _run_with_workflow_token(thread_id, lambda: handler(request))
        return _tool_message_for_request(
            _blocked_message(change, already_rejected=state == "rejected"), request
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        change = self._change_for_request(request)
        if change is None:
            return handler(request)

        async def run_handler() -> ToolMessage | Command:
            return handler(request)

        return _run_coroutine_sync(
            self._handle_change_async(request, lambda _request: run_handler(), change)
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
