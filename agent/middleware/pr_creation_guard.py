"""Block shell fallbacks that create pull requests outside open_pull_request."""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
_SHELL_EXECUTABLES = {"bash", "dash", "sh", "zsh"}
_MAX_SHELL_EXPANSION_DEPTH = 3
_SHELL_EXPANSION_DEPTH_LIMIT_TOKEN = "__pr_creation_guard_shell_expansion_depth_limit__"
_GITHUB_PULLS_ENDPOINT = re.compile(r"(?:^|/)repos/[^/\s]+/[^/\s]+/pulls/?$")
_GITHUB_PULLS_URL = re.compile(r"https://api\.github\.com/repos/[^/\s]+/[^/\s]+/pulls/?")
_BLOCK_ERROR = (
    "New pull requests must be opened with the open_pull_request tool so the PR is "
    "attributed to the triggering user. If open_pull_request failed, surface that "
    "failure instead of falling back to gh pr create, gh api /pulls, curl, or another "
    "direct PR creation path."
)


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


def _split_shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _executable_name(token: str) -> str:
    return os.path.basename(token.strip("'\""))


def _shell_command_argument(tokens: list[str], shell_index: int) -> str | None:
    for index, token in enumerate(tokens[shell_index + 1 :], start=shell_index + 1):
        if token in _SHELL_SEPARATORS:
            return None
        if token == "-c" or (
            token.startswith("-") and not token.startswith("--") and "c" in token[1:]
        ):
            if index + 1 < len(tokens) and tokens[index + 1] not in _SHELL_SEPARATORS:
                return tokens[index + 1]
            return None
    return None


def _has_nested_shell_command(tokens: list[str]) -> bool:
    return any(
        _executable_name(token) in _SHELL_EXECUTABLES
        and _shell_command_argument(tokens, index) is not None
        for index, token in enumerate(tokens)
    )


def _expand_nested_shell_tokens(tokens: list[str], depth: int = 0) -> list[str]:
    if depth >= _MAX_SHELL_EXPANSION_DEPTH:
        if _has_nested_shell_command(tokens):
            return [*tokens, _SHELL_EXPANSION_DEPTH_LIMIT_TOKEN]
        return tokens

    expanded = list(tokens)
    for index, token in enumerate(tokens):
        if _executable_name(token) not in _SHELL_EXECUTABLES:
            continue
        inner_command = _shell_command_argument(tokens, index)
        if inner_command is None:
            continue
        expanded.extend(_expand_nested_shell_tokens(_split_shell_tokens(inner_command), depth + 1))
    return expanded


def _shell_tokens(command: str) -> list[str]:
    return _expand_nested_shell_tokens(_split_shell_tokens(command))


def _is_assignment(token: str) -> bool:
    name, sep, _value = token.partition("=")
    return bool(sep and name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _gh_subtokens(tokens: list[str], index: int) -> list[str]:
    subtokens: list[str] = []
    for token in tokens[index + 1 :]:
        if token in _SHELL_SEPARATORS:
            break
        subtokens.append(token)
    return subtokens


def _contains_gh_pr_create(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if _executable_name(token) != "gh":
            continue
        subtokens = _gh_subtokens(tokens, index)
        for offset, subtoken in enumerate(subtokens[:-1]):
            if subtoken == "pr" and subtokens[offset + 1] == "create":
                return True
    return False


_GH_API_VALUE_FLAGS = {
    "-X",
    "--method",
    "-H",
    "--header",
    "-F",
    "--field",
    "-f",
    "--raw-field",
    "--hostname",
    "--input",
    "-q",
    "--jq",
    "-p",
    "--preview",
    "--cache",
    "-t",
    "--template",
}


def _gh_api_endpoint(subtokens: list[str]) -> str | None:
    for index, token in enumerate(subtokens):
        if token != "api":
            continue
        skip_next = False
        for candidate in subtokens[index + 1 :]:
            if skip_next:
                skip_next = False
                continue
            if candidate.startswith("-"):
                if "=" not in candidate and candidate in _GH_API_VALUE_FLAGS:
                    skip_next = True
                continue
            if _is_assignment(candidate):
                continue
            return candidate.strip("'\"")
    return None


def _gh_api_uses_post_or_body(subtokens: list[str]) -> bool:
    body_flags = {"-f", "--field", "-F", "--raw-field", "--input"}
    for index, token in enumerate(subtokens):
        upper = token.upper()
        if upper in {"-XPOST", "--METHOD=POST"}:
            return True
        if token in {"-X", "--method"} and index + 1 < len(subtokens):
            if subtokens[index + 1].upper() == "POST":
                return True
        if token.startswith("--method=") and token.split("=", 1)[1].upper() == "POST":
            return True
        if token in body_flags or any(token.startswith(f"{flag}=") for flag in body_flags):
            return True
    return False


def _contains_gh_api_pull_create(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if _executable_name(token) != "gh":
            continue
        subtokens = _gh_subtokens(tokens, index)
        if "api" not in subtokens:
            continue
        endpoint = _gh_api_endpoint(subtokens)
        if (
            endpoint
            and _GITHUB_PULLS_ENDPOINT.search(endpoint)
            and _gh_api_uses_post_or_body(subtokens)
        ):
            return True
    return False


def _contains_direct_pull_create(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if _executable_name(token) != "curl":
            continue
        subtokens: list[str] = []
        for candidate in tokens[index + 1 :]:
            if candidate in _SHELL_SEPARATORS:
                break
            subtokens.append(candidate)
        if not any(_GITHUB_PULLS_URL.search(candidate) for candidate in subtokens):
            continue
        has_post = any(
            token.upper() in {"-XPOST", "--REQUEST=POST"}
            or (
                token in {"-X", "--request"}
                and idx + 1 < len(subtokens)
                and subtokens[idx + 1].upper() == "POST"
            )
            or (token.startswith("--request=") and token.split("=", 1)[1].upper() == "POST")
            for idx, token in enumerate(subtokens)
        )
        has_body = any(token in {"-d", "--data", "--data-raw", "--json"} for token in subtokens)
        if has_post or has_body:
            return True
    return False


def is_pr_creation_fallback_command(command: str) -> bool:
    tokens = _shell_tokens(command)
    return (
        _SHELL_EXPANSION_DEPTH_LIMIT_TOKEN in tokens
        or _contains_gh_pr_create(tokens)
        or _contains_gh_api_pull_create(tokens)
        or _contains_direct_pull_create(tokens)
    )


def _blocked_tool_message(request: ToolCallRequest, command: str) -> ToolMessage:
    content = {
        "status": "error",
        "error_type": "PullRequestCreationFallbackBlocked",
        "code": "pr_creation_fallback_blocked",
        "recoverable_by_agent": False,
        "error": _BLOCK_ERROR,
        "blocked_command": command,
    }
    return ToolMessage(
        content=json.dumps(content),
        tool_call_id=_tool_call_id(request),
        status="error",
    )


class PullRequestCreationGuardMiddleware(AgentMiddleware):
    """Prevent attributed-PR failures from being hidden by shell fallbacks."""

    state_schema = AgentState

    def _blocked_message_for_request(self, request: ToolCallRequest) -> ToolMessage | None:
        if _tool_name(request) != "execute":
            return None
        command = _tool_args(request).get("command")
        if not isinstance(command, str) or not is_pr_creation_fallback_command(command):
            return None
        return _blocked_tool_message(request, command)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._blocked_message_for_request(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._blocked_message_for_request(request)
        if blocked is not None:
            return blocked
        return await handler(request)
