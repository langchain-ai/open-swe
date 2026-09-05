"""Guard mutating GitHub calls by repository owner and approval scope."""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.dashboard.workflow_approval import (
    ensure_workflow_push_pending,
    workflow_push_approved,
)
from agent.middleware._shell_parsing import (
    _SHELL_EXPANSION_DEPTH_LIMIT_TOKEN,
    executable_name,
    gh_api_endpoint,
    gh_api_uses_mutation,
    gh_subtokens,
    shell_tokens,
)
from agent.run_config import RunConfig

_REPO = re.compile(r"^([^/\s]+)/([^/\s]+)$")
_REPOS_PATH = re.compile(r"(?:^|/)repos/([^/\s]+)/([^/\s]+)(?:/|$)")
_MUTATING_GH = (
    {
        ("issue", action)
        for action in (
            "create",
            "comment",
            "close",
            "reopen",
            "edit",
            "delete",
            "lock",
            "pin",
            "transfer",
        )
    }
    | {
        ("pr", action)
        for action in ("comment", "review", "close", "reopen", "edit", "merge", "ready", "lock")
    }
    | {("release", action) for action in ("create", "edit", "delete", "upload")}
    | {("repo", action) for action in ("create", "edit", "delete", "archive", "fork")}
)


def _tool_name(request: ToolCallRequest) -> str | None:
    call = getattr(request, "tool_call", None)
    return (
        call.get("name")
        if isinstance(call, Mapping) and isinstance(call.get("name"), str)
        else None
    )


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    call = getattr(request, "tool_call", None)
    args = call.get("args") if isinstance(call, Mapping) else None
    return dict(args) if isinstance(args, Mapping) else {}


def _tool_call_id(request: ToolCallRequest) -> str | None:
    call = getattr(request, "tool_call", None)
    value = call.get("id") if isinstance(call, Mapping) else None
    return value if isinstance(value, str) else None


def _config(request: ToolCallRequest) -> RunConfig:
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None)
    return RunConfig.from_config(config)


def _repo(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    match = _REPO.fullmatch(value.strip().strip("'\""))
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _repo_from_text(value: str) -> str | None:
    match = _REPOS_PATH.search(value)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _flag_value(tokens: list[str], names: set[str]) -> str | None:
    for index, token in enumerate(tokens):
        if token in names and index + 1 < len(tokens):
            return _repo(tokens[index + 1])
        if any(token.startswith(f"{name}=") for name in names):
            return _repo(token.split("=", 1)[1])
        if token.startswith("-R") and token != "-R":
            return _repo(token[2:])
    return None


def _gh_operation(tokens: list[str]) -> tuple[str, str | None] | None:
    for index, token in enumerate(tokens):
        if executable_name(token) != "gh":
            continue
        subtokens = gh_subtokens(tokens, index)
        if "api" in subtokens:
            endpoint = gh_api_endpoint(subtokens)
            if not gh_api_uses_mutation(subtokens):
                return None
            return "gh api", _repo_from_text(endpoint or "") or _flag_value(
                subtokens, {"--repo", "-R"}
            )
        command = next((item for item in subtokens if not item.startswith("-")), None)
        if command is None:
            continue
        command_index = subtokens.index(command)
        action = next(
            (item for item in subtokens[command_index + 1 :] if not item.startswith("-")), None
        )
        if (command, action) not in _MUTATING_GH:
            continue
        target = _flag_value(subtokens, {"--repo", "-R"})
        if target is None:
            target = next(
                (_repo(item) for item in subtokens[command_index + 1 :] if _repo(item)), None
            )
        return f"gh {command} {action}", target
    return None


def _curl_operation(tokens: list[str]) -> tuple[str, str | None] | None:
    for index, token in enumerate(tokens):
        if executable_name(token) != "curl":
            continue
        subtokens = gh_subtokens(tokens, index)
        urls = [item for item in subtokens if item.startswith(("http://", "https://"))]
        url = next(
            (item for item in urls if urlparse(item).hostname in {"api.github.com", "github.com"}),
            None,
        )
        if url is None:
            continue
        method = None
        body = False
        for offset, item in enumerate(subtokens):
            if item in {"-X", "--request"} and offset + 1 < len(subtokens):
                method = subtokens[offset + 1].upper()
            elif item.startswith("--request=") or item.startswith("-X") and len(item) > 2:
                method = (
                    item.split("=", 1)[-1][2:].upper()
                    if item.startswith("-X")
                    else item.split("=", 1)[1].upper()
                )
            elif item in {
                "-d",
                "--data",
                "--data-raw",
                "--data-binary",
                "--json",
            } or item.startswith("--data="):
                body = True
        if method not in {None, "GET", "HEAD"} or body:
            return "curl", _repo_from_text(url)
    return None


def _operation(request: ToolCallRequest) -> tuple[str, str | None] | None:
    name = _tool_name(request)
    args = _tool_args(request)
    if name in {"execute", "background_execute"} and isinstance(args.get("command"), str):
        tokens = shell_tokens(args["command"])
        if _SHELL_EXPANSION_DEPTH_LIMIT_TOKEN in tokens:
            return "shell command", None
        return _gh_operation(tokens) or _curl_operation(tokens)
    if name == "http_request":
        url = args.get("url")
        method = str(args.get("method") or "GET").upper()
        if (
            isinstance(url, str)
            and urlparse(url).hostname in {"api.github.com", "github.com"}
            and method not in {"GET", "HEAD"}
        ):
            return f"http_request {method}", _repo_from_text(url)
    return None


def _approved_owners(config: RunConfig) -> set[str]:
    owners = {config.repo.owner.lower()} if config.repo and config.repo.owner else set()
    owners.update(owner.lower() for owner in config.approved_repo_owners if owner)
    for key in ("workspace_owner", "triggering_workspace_owner", "organization", "org"):
        value = config.get(key)
        if isinstance(value, str) and value:
            owners.add(value.lower())
    return owners


def _blocked(
    request: ToolCallRequest, operation: str, target: str | None, approved: set[str]
) -> ToolMessage:
    payload = {
        "status": "error",
        "error_type": "ExternalRepoWriteBlocked",
        "error": "Draft the proposed content and send it to the requester for review instead of publishing it.",
        "target_repo": target,
        "operation": operation,
        "approved_scope": sorted(approved),
    }
    return ToolMessage(
        content=json.dumps(payload), tool_call_id=_tool_call_id(request), status="error"
    )


class ExternalRepoWriteGuardMiddleware(AgentMiddleware):
    """Block or approve mutating GitHub calls outside the run repository."""

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        operation = _operation(request)
        if operation is None:
            return await handler(request)
        operation_kind, target = operation
        config = _config(request)
        target = target or config.repo_full_name
        approved = _approved_owners(config)
        if not target or "/" not in target or target.split("/", 1)[0].lower() not in approved:
            return _blocked(request, operation_kind, target, approved)
        if target.lower() == config.repo_full_name.lower():
            return await handler(request)
        thread_id = config.thread_id
        if not thread_id:
            return _blocked(request, operation_kind, target, approved)
        fingerprint = hashlib.sha256(
            f"{operation_kind}:{target}:{json.dumps(_tool_args(request), sort_keys=True, default=str)}".encode()
        ).hexdigest()[:16]
        if await workflow_push_approved(thread_id, fingerprint):
            return await handler(request)
        await ensure_workflow_push_pending(
            thread_id,
            fingerprint=fingerprint,
            repo=target,
            branch="",
            base_sha="",
            head_sha="",
            files=[],
            diff_preview=operation_kind,
            approval_url=None,
            operation_kind=operation_kind,
        )
        payload = {
            "status": "error",
            "error_type": "ExternalRepoWriteApprovalRequired",
            "error": "Human approval is required before writing to an allowed repository outside the run target.",
            "target_repo": target,
            "operation": operation_kind,
            "approved_scope": sorted(approved),
            "approval_required": True,
            "fingerprint": fingerprint,
        }
        return ToolMessage(
            content=json.dumps(payload), tool_call_id=_tool_call_id(request), status="error"
        )
