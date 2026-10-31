"""Gate workflow-file pushes on human approval."""

import hashlib
import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langgraph_sdk import get_client

from openswe.middleware.pr_creation_guard import executable_name, shell_commands, shell_tokens
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SandboxUnreachableError, get_sandbox_backend
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
_SHELL_OPERATORS = {";", "|", "||", "&"}
_REF_NAME = re.compile(r"^[A-Za-z0-9._/@+-]+$")
_GIT_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_UNSAFE_RAW_COMMAND = re.compile(r"[;|`$<>\n\r]")
_DIFF_PREVIEW_MAX_CHARS = 20_000
_DIFF_PREVIEW_MAX_LINES = 400


class WorkflowPushInspectionError(RuntimeError):
    """A push could not be inspected safely."""


@dataclass(frozen=True)
class ParsedGitPush:
    repo_dir: str | None
    remote: str
    local_ref: str
    remote_ref: str
    set_upstream: bool = False
    force_with_lease: str | None = None
    force_flags: tuple[str, ...] = ()

    @property
    def remote_branch(self) -> str:
        return self.remote_ref.removeprefix("refs/heads/")

    @property
    def forced(self) -> bool:
        return self.force_with_lease is not None or bool(self.force_flags)

    def inspection_failed(self) -> None:
        raise WorkflowPushInspectionError(
            "Cannot inspect this push. Push the current branch to the same branch on origin "
            "and ensure the repository can be inspected. Nothing ran."
        )

    def command_for_head(self, repo_dir: str, head_sha: str, lease_sha: str) -> str:
        """The push of ``head_sha`` the owner approves.

        A forced push is leased to ``lease_sha``, the remote tip the approval diff was
        taken against (empty when the branch did not exist), so it cannot overwrite
        remote commits the owner never saw.
        """
        args = ["push"]
        if self.set_upstream:
            args.append("--set-upstream")
        if self.forced:
            args.append(f"--force-with-lease=refs/heads/{self.remote_branch}:{lease_sha}")
            if "--force-if-includes" in self.force_flags:
                args.append("--force-if-includes")
        args.extend([self.remote, f"{head_sha}:refs/heads/{self.remote_branch}"])
        return _git_command(repo_dir, " ".join(shlex.quote(arg) for arg in args))


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


@dataclass(frozen=True)
class GitInspectResult:
    output: str
    ok: bool


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


async def _backend(thread_id: str | None) -> SandboxBackendProtocol | None:
    if not thread_id:
        return None
    try:
        return await get_sandbox_backend(thread_id)
    except Exception as exc:
        raise SandboxUnreachableError(
            thread_id, None, "Cannot resolve the sandbox for push inspection", reason=str(exc)
        ) from exc


# What deepagents' local shell backend reports for a command that printed nothing.
_NO_OUTPUT = "<no output>"


def _response_output(response: Any) -> str:
    output = getattr(response, "output", None)
    if not isinstance(output, str):
        value = response.get("output") if isinstance(response, Mapping) else None
        output = value if isinstance(value, str) else str(response or "")
    return "" if output.strip() == _NO_OUTPUT else output


def _response_ok(response: Any) -> bool:
    exit_code = getattr(response, "exit_code", None)
    if isinstance(exit_code, int):
        return exit_code == 0
    if isinstance(response, Mapping):
        value = response.get("exit_code")
        if isinstance(value, int):
            return value == 0
    return True


# Control operators, which a shell honors with or without surrounding spaces.
_SHELL_OPERATOR = re.compile(r"(&&|\|\||[;&|()])")


def _command_words(command: str) -> list[str]:
    """``command``'s words, with control operators split off even where written unspaced."""
    return [
        part for token in shell_tokens(command) for part in _SHELL_OPERATOR.split(token) if part
    ]


def _chained_push_dirs(command: str) -> list[str | None]:
    """Repositories pushed from by a command containing other shell operations."""
    if _standalone_push_args(command) is not None:
        return []
    tokens = _command_words(command)
    dirs: list[str | None] = []
    cwd: str | None = None
    for index, token in enumerate(tokens):
        if token == "cd" and index + 1 < len(tokens):
            cwd = tokens[index + 1]
            continue
        if executable_name(token) != "git":
            continue
        repo_dir = cwd
        position = index + 1
        while position < len(tokens) and tokens[position].startswith("-"):
            if tokens[position] in {"-C", "-c"} and position + 1 < len(tokens):
                if tokens[position] == "-C":
                    repo_dir = tokens[position + 1]
                position += 2
                continue
            position += 1
        if position < len(tokens) and tokens[position] == "push":
            dirs.append(repo_dir)
    return dirs


async def _may_push_workflow_changes(backend: Any, repo_dir: str | None) -> bool:
    """Whether a push from ``repo_dir`` could carry ``.github/workflows`` changes.

    Pending edits count: a chained ``git commit`` may commit them before the push.
    A repository that cannot be inspected counts too, such as one the same
    command clones: nothing shows what it would push.
    """
    pending = await _run_git(
        backend, repo_dir, "status --porcelain --untracked-files=all -- .github/workflows"
    )
    if not pending.ok or pending.output:
        return True
    for base in ("@{upstream}", "origin/HEAD"):
        unpushed = await _run_git(
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


def _unsupported_push_message(request: ToolCallRequest) -> ToolMessage:
    content = {
        "status": "error",
        "error_type": "WorkflowPushUnsupportedFlags",
        "error": (
            "Open SWE cannot inspect this standalone git push. Nothing ran. Use "
            "`git [-C <repo>] push [options] origin <branch-or-refspec>` with optional "
            "-u/--set-upstream, --force-with-lease[=<ref>[:<sha>]], --force-if-includes, "
            "-f, or --force, targeting the current branch."
        ),
    }
    return ToolMessage(
        content=json.dumps(content), tool_call_id=_tool_call_id(request) or "", status="error"
    )


def _parse_git_push(command: str) -> ParsedGitPush | None:
    args = _standalone_push_args(command)
    if args is None:
        return None
    tokens, repo_dir = args
    return _parse_push_args(tokens, repo_dir=repo_dir)


def _standalone_push_args(command: str) -> tuple[list[str], str | None] | None:
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
        return _git_push_args(tokens[3:], repo_dir=tokens[1])

    if any(token in _SHELL_OPERATORS or token == "&&" for token in tokens):
        return None
    return _git_push_args(tokens, repo_dir=None)


def _git_push_args(
    tokens: list[str], *, repo_dir: str | None
) -> tuple[list[str], str | None] | None:
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
    return tokens[i + 1 :], repo_dir


def _parse_push_args(tokens: list[str], *, repo_dir: str | None) -> ParsedGitPush | None:
    set_upstream = False
    force_with_lease: str | None = None
    force_flags: list[str] = []
    positional: list[str] = []
    for token in tokens:
        if token in {"-u", "--set-upstream"}:
            set_upstream = True
        elif token == "--force-with-lease" or token.startswith("--force-with-lease="):
            if force_with_lease is not None or not _safe_force_with_lease(token):
                return None
            force_with_lease = token
        elif token in {"-f", "--force", "--force-if-includes"}:
            if token not in force_flags:
                force_flags.append(token)
        elif token.startswith("-"):
            return None
        else:
            positional.append(token)
    if len(positional) != 2 or positional[0] != "origin":
        return None
    parsed = _parse_refspec(positional[1])
    if parsed is None:
        return None
    local_ref, remote_ref = parsed
    return ParsedGitPush(
        repo_dir=repo_dir,
        remote="origin",
        local_ref=local_ref,
        remote_ref=remote_ref,
        set_upstream=set_upstream,
        force_with_lease=force_with_lease,
        force_flags=tuple(force_flags),
    )


def _safe_force_with_lease(option: str) -> bool:
    if option == "--force-with-lease":
        return True
    ref, separator, expected = option.partition("=")[2].partition(":")
    return _safe_ref(ref, allow_head=False) and (
        not separator or not expected or bool(_GIT_OBJECT_ID.fullmatch(expected))
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


async def _run_git(backend: Any, repo_dir: str | None, args: str) -> GitInspectResult:
    try:
        response = await backend.aexecute(_git_command(repo_dir, args), timeout=30)
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


async def _inherited_workflow_source(backend: Any, root: str, head: str) -> str | None:
    parents = await _run_git(backend, root, f"rev-list --parents -n 1 {shlex.quote(head)}")
    parts = _first_line(parents.output).split() if parents.ok else []
    if len(parts) != 3:
        return None
    first_parent, merged_parent = parts[1:]
    if first_parent == merged_parent:
        return None
    origin_head = await _run_git(backend, root, "symbolic-ref --short refs/remotes/origin/HEAD")
    source_ref = _first_line(origin_head.output) if origin_head.ok else ""
    if not source_ref:
        return None
    source_contains_parent = await _run_git(
        backend,
        root,
        f"merge-base --is-ancestor {shlex.quote(merged_parent)} {shlex.quote(source_ref)}",
    )
    if not source_contains_parent.ok:
        return None
    feature_workflow_diff = await _run_git(
        backend,
        root,
        f"diff --quiet {shlex.quote(first_parent)} {shlex.quote(head)} -- .github/workflows",
    )
    if feature_workflow_diff.ok:
        return None
    merged_workflow_diff = await _run_git(
        backend,
        root,
        f"diff --quiet {shlex.quote(merged_parent)} {shlex.quote(head)} -- .github/workflows",
    )
    return source_ref.removeprefix("origin/") if merged_workflow_diff.ok else None


def _approval_url(thread_id: str | None, fingerprint: str) -> str | None:
    if not thread_id:
        return None
    return dashboard_workflow_approval_url(thread_id, fingerprint)


async def _new_branch_base(backend: SandboxBackendProtocol, root: str, head: str) -> str:
    upstream = await _run_git(backend, root, "rev-parse --symbolic-full-name '@{upstream}'")
    if upstream.ok and upstream.output:
        merge_base = await _run_git(
            backend,
            root,
            f"merge-base {shlex.quote(head)} {shlex.quote(_first_line(upstream.output))}",
        )
        if merge_base.ok and merge_base.output:
            return _first_line(merge_base.output)

    candidates = await _run_git(
        backend, root, f"rev-list --boundary --timestamp {shlex.quote(head)} --not --remotes=origin"
    )
    if not candidates.ok:
        return ""
    if not candidates.output:
        return head
    boundaries: list[tuple[int, str]] = []
    for line in candidates.output.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit():
            return ""
        timestamp, commit = parts
        if commit.startswith("-") and _GIT_OBJECT_ID.fullmatch(commit[1:]):
            boundaries.append((int(timestamp), commit[1:]))
    return max(boundaries, key=lambda boundary: boundary[0])[1] if boundaries else ""


async def _workflow_change_for_push(
    backend: SandboxBackendProtocol, parsed: ParsedGitPush
) -> WorkflowPushChange | None:
    root_result = await _run_git(backend, parsed.repo_dir, "rev-parse --show-toplevel")
    if not root_result.ok:
        return parsed.inspection_failed()
    root = _first_line(root_result.output)
    if not root:
        return parsed.inspection_failed()

    target_sha = await _run_git(backend, root, f"rev-parse {shlex.quote(parsed.local_ref)}")
    head = _first_line(target_sha.output) if target_sha.ok else ""
    if not head or not _GIT_OBJECT_ID.fullmatch(head):
        return parsed.inspection_failed()

    remote_branch = f"refs/remotes/{parsed.remote}/{parsed.remote_branch}"
    remote_branch_exists = await _run_git(
        backend, root, f"rev-parse --verify {shlex.quote(remote_branch)}"
    )
    if remote_branch_exists.ok and _first_line(remote_branch_exists.output):
        base_result = await _run_git(backend, root, f"rev-parse {shlex.quote(remote_branch)}")
        base_sha = _first_line(base_result.output) if base_result.ok else ""
        lease_sha = base_sha
        if parsed.forced and not _GIT_OBJECT_ID.fullmatch(lease_sha):
            return parsed.inspection_failed()
    else:
        base_sha = await _new_branch_base(backend, root, head)
        lease_sha = ""
    if not base_sha:
        return parsed.inspection_failed()
    range_expr = f"{shlex.quote(base_sha)}..{shlex.quote(head)}"

    names = await _run_git(
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

    branch = await _run_git(backend, root, "rev-parse --abbrev-ref HEAD")
    branch_name = _first_line(branch.output) if branch.ok else ""
    if not branch_name or branch_name == "HEAD" or parsed.remote_branch != branch_name:
        return parsed.inspection_failed()
    if parsed.local_ref not in {"HEAD", branch_name, f"refs/heads/{branch_name}"}:
        return parsed.inspection_failed()

    diff = await _run_git(
        backend, root, f"diff --binary --full-index {range_expr} -- .github/workflows"
    )
    if not diff.ok or not diff.output:
        return parsed.inspection_failed()
    numstat = await _run_git(backend, root, f"diff --numstat {range_expr} -- .github/workflows")
    diff_preview, diff_preview_truncated = _diff_preview(diff.output)
    diff_stats = _diff_stats(files, numstat.output if numstat.ok else "")
    inherited_from = await _inherited_workflow_source(backend, root, head)

    remote = await _run_git(backend, root, "config --get remote.origin.url")
    repo = _normalize_remote(_first_line(remote.output)) if remote.ok else ""
    fixed_command = parsed.command_for_head(root, head, lease_sha)
    payload: dict[str, object] = {
        "repo": repo,
        "files": files,
        "diff": diff.output,
        "remote": parsed.remote,
        "force_with_lease": parsed.force_with_lease,
    }
    if not repo.startswith(("https://", "http://", "ssh://", "git://", "git@")):
        payload["repo_dir"] = root
    if not repo or parsed.forced:
        payload.update(
            branch=branch_name,
            base_sha=base_sha,
            head_sha=head,
            local_ref=parsed.local_ref,
            remote_ref=parsed.remote_ref,
        )
    if parsed.force_flags:
        payload["force_flags"] = parsed.force_flags
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
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}{web_review}\n\n"
        "Approval covers this workflow diff on branches in this thread and repository. "
        "Different workflow diffs and lease-protected pushes require separate approval."
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
            parsed = _parse_git_push(command)
            if parsed is None:
                continue
            backend = await _backend(_thread_id(request))
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
            if change is None:
                if await self._unsupported_workflow_push(request):
                    return _unsupported_push_message(request)
                if await self._chained_workflow_push(request):
                    return _chained_push_message(request)
        except (WorkflowPushInspectionError, SandboxUnreachableError) as exc:
            return ToolMessage(
                content=json.dumps(
                    {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                ),
                tool_call_id=_tool_call_id(request) or "",
                status="error",
            )
        if change is not None:
            return await self._handle_change_async(request, handler, change)
        return await handler(request)

    async def _unsupported_workflow_push(self, request: ToolCallRequest) -> bool:
        """Whether ``request`` is a standalone push with options the guard cannot inspect."""
        dirs = [
            args[1]
            for command in shell_commands(request)
            if (args := _standalone_push_args(command)) is not None
            and _parse_push_args(args[0], repo_dir=args[1]) is None
        ]
        backend = await _backend(_thread_id(request)) if dirs else None
        if backend is None:
            return bool(dirs)
        for repo_dir in dirs:
            if await _may_push_workflow_changes(backend, repo_dir):
                return True
        return False

    async def _chained_workflow_push(self, request: ToolCallRequest) -> bool:
        """Whether ``request`` pushes workflow changes as one step of a longer command."""
        dirs = [
            repo_dir
            for command in shell_commands(request)
            for repo_dir in _chained_push_dirs(command)
        ]
        backend = await _backend(_thread_id(request)) if dirs else None
        if backend is None:
            return bool(dirs)
        for repo_dir in dirs:
            if await _may_push_workflow_changes(backend, repo_dir):
                return True
        return False
