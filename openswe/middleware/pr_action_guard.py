"""Require author consent before shell commands close or merge a pull request."""

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from langchain_core.messages import ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from openswe.act_as.gate import require_consent
from openswe.github.pull_request_status import PullRequestClient
from openswe.input_messages import latest_input_is_relay, latest_input_sender_id
from openswe.middleware.pr_creation_guard import shell_tokens
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.users import User

logger = logging.getLogger(__name__)

_PR_URL = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/([1-9][0-9]*)/?")
_API_PR = re.compile(
    r"(?:repos/[^/\s]+/[^/\s]+/(?:pulls|issues)/[0-9]+(?:/merge)?(?:[\s?\"']|$)|(?:close|merge)PullRequest)"
)
_WRITE = re.compile(
    r"(?:\b(?:PATCH|PUT|POST)\b|-X(?:PATCH|PUT|POST)|--(?:raw-)?field|-\w*[fFd]|--(?:data|json|input)|state.?=.?closed)",
    re.I,
)
_UNSAFE_SHELL = re.compile(r"[;&|`$<>\n\r]")
_VALUE_FLAGS = {
    "--comment",
    "-c",
    "--body",
    "-b",
    "--subject",
    "-t",
    "--author-email",
    "-A",
    "--match-head-commit",
}
_FLAGS = {
    "--delete-branch",
    "-d",
    "--squash",
    "-s",
    "--merge",
    "-m",
    "--rebase",
    "-r",
    "--auto",
    "--disable-auto",
    "--admin",
}


@dataclass(frozen=True)
class PullRequestAction:
    action: Literal["close", "merge"]
    owner: str
    repo: str
    number: int
    flags: tuple[str, ...]

    @property
    def command(self) -> str:
        url = f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"
        return shlex.join(["gh", "pr", self.action, url, *self.flags])

    async def consent(
        self, cfg: RunConfig, *, relayed: bool, actor: str | None
    ) -> dict[str, object] | None:
        async with PullRequestClient.as_app(self.owner, self.repo, self.number) as pull:
            payload = await pull.pull()
        user = payload.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login:
            raise ValueError("Could not identify the PR author")
        if login.lower() == (actor or "").lower() and not relayed:
            return None
        head, base = payload.get("head"), payload.get("base")
        title = payload.get("title")
        refusal = await require_consent(
            thread_id=cfg.thread_id,
            token_kind="app",
            author=login,
            owner=self.owner,
            repo=self.repo,
            head=str(head.get("ref", "")) if isinstance(head, dict) else "",
            base=str(base.get("ref", "")) if isinstance(base, dict) else "",
            title=title if isinstance(title, str) else "",
            action=self.action,
            relayed=relayed,
        )
        return dict(refusal) if refusal is not None else None


async def _parse_action(command: str, cfg: RunConfig) -> PullRequestAction:
    if _UNSAFE_SHELL.search(command):
        raise ValueError("Use a standalone gh pr close or gh pr merge command with a PR URL")
    tokens = shlex.split(command)
    if len(tokens) < 3 or PurePosixPath(tokens[0]).name != "gh" or tokens[1] != "pr":
        raise ValueError("Use a standalone gh pr close or gh pr merge command with a PR URL")
    action = tokens[2]
    if action not in {"close", "merge"}:
        raise ValueError("Unsupported PR action")
    repo = f"{cfg.repo.owner}/{cfg.repo.name}" if cfg.repo else ""
    selector: str | None = None
    flags: list[str] = []
    remaining = iter(tokens[3:])
    for token in remaining:
        name, separator, value = token.partition("=")
        if name in {"--repo", "-R"}:
            repo = value if separator else next(remaining, "")
        elif name in _VALUE_FLAGS:
            value = value if separator else next(remaining, "")
            if not value:
                raise ValueError(f"Missing value for {name}")
            flags.extend([name, value])
        elif token in _FLAGS:
            flags.append(token)
        elif not token.startswith("-") and selector is None:
            selector = token
        else:
            raise ValueError("Use an explicit PR URL and supported gh PR flags")
    if selector and (matched := _PR_URL.fullmatch(selector)):
        owner, name, number = matched.groups()
    elif selector and re.fullmatch(r"[1-9][0-9]*", selector):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("A repository or canonical PR URL is required")
        owner, name = repo.split("/")
        number = selector
    else:
        backend = SANDBOX_BACKENDS.get(cfg.thread_id or "")
        if backend is None:
            raise ValueError("Use an explicit PR number with --repo or a canonical PR URL")
        inspect = ["gh", "pr", "view", *([selector] if selector else [])]
        if repo:
            inspect.extend(["--repo", repo])
        inspect.extend(["--json", "url"])
        response = await backend.aexecute(shlex.join(inspect))
        if response.exit_code != 0 or response.truncated:
            raise ValueError("Could not resolve the PR selector")
        payload: object = json.loads(response.output)
        url = payload.get("url") if isinstance(payload, dict) else None
        matched = _PR_URL.fullmatch(url) if isinstance(url, str) else None
        if matched is None:
            raise ValueError("Could not resolve a canonical PR URL")
        owner, name, number = matched.groups()
    return PullRequestAction(action, owner, name, int(number), tuple(flags))


def _has_relay(request: ToolCallRequest, cfg: RunConfig) -> bool:
    if cfg.relayed_from_thread:
        return True
    return latest_input_is_relay(request.state.get("messages", []))


async def _actor_login(request: ToolCallRequest, cfg: RunConfig) -> str | None:
    sender = latest_input_sender_id(request.state.get("messages", []))
    if sender is None:
        return cfg.github_login
    platform, _, identity = sender.partition(":")
    if platform == "user":
        person = await User.get(UUID(identity))
        return person.github_login if person is not None else None
    if platform == "github" and not identity.isdigit():
        return identity
    person = await User.for_person({"id": sender})
    return person.github_login if person is not None else None


def _blocked(request: ToolCallRequest, error: str | dict[str, object]) -> ToolMessage:
    payload = {"success": False, "error": error} if isinstance(error, str) else error
    return ToolMessage(
        content=json.dumps(payload), tool_call_id=request.tool_call["id"], status="error"
    )


class PullRequestActionGuardMiddleware(OpenSWEMiddleware):
    """Gate shell PR lifecycle actions, including delegated and background tool calls."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        raise NotImplementedError("PR consent requires async tool calls")

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] not in {"execute", "background_execute"}:
            return await handler(request)
        command = request.tool_call["args"].get("command")
        if not isinstance(command, str):
            return await handler(request)
        tokens = shell_tokens(command)
        lifecycle = bool(re.search(r"\bgh\s+pr\s+(?:close|merge)\b", command)) or any(
            PurePosixPath(token).name == "gh"
            and any(
                tokens[position : position + 2] in (["pr", "close"], ["pr", "merge"])
                for position in range(index + 1, len(tokens))
            )
            for index, token in enumerate(tokens)
        )
        if _API_PR.search(command) and (
            _WRITE.search(command) or "closePullRequest" in command or "mergePullRequest" in command
        ):
            return _blocked(
                request, "Use gh pr close or gh pr merge with a PR URL for author consent"
            )
        if re.search(r"\b(?:gh\s+api\s+graphql|api\.github\.com/graphql)\b", command) and (
            "--input" in command or "--data" in command or "--json" in command
        ):
            return _blocked(request, "Opaque GraphQL mutations cannot bypass PR author consent")
        if any("shell_expansion_depth_limit" in token for token in tokens):
            return _blocked(request, "Nested shell commands cannot bypass PR consent checks")
        if not lifecycle:
            return await handler(request)
        cfg = RunConfig.from_config(request.runtime.config)
        try:
            action = await _parse_action(command, cfg)
            relayed = _has_relay(request, cfg)
            refusal = await action.consent(
                cfg,
                relayed=relayed,
                actor=await _actor_login(request, cfg) if not relayed else None,
            )
        except Exception:
            logger.exception("Could not authorize PR lifecycle action")
            return _blocked(
                request,
                "PR action blocked: author consent could not be verified. Use a standalone gh pr close or gh pr merge command with a canonical PR URL.",
            )
        if refusal is not None:
            return _blocked(request, refusal)
        tool_call: ToolCall = {
            **request.tool_call,
            "args": {**request.tool_call["args"], "command": action.command},
        }
        return await handler(request.override(tool_call=tool_call))
