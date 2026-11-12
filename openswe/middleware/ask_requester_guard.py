"""Record whoever has an Ask channel run push to a pull request as one of its requesters.

Robin authors Ask channel pull requests as itself, so the ``Robin Approves`` check must
exclude everyone whose request put commits on the branch, not only the person who
opened it. Before an Ask run pushes, its requester is added to every open pull request
linked to the thread; the push doesn't run if that fails.
"""

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from openswe.credential_scope import GitHubCredentialScope
from openswe.github.approval_policy import PullRequestApproval
from openswe.github.pull_requests import PullRequest
from openswe.middleware.pr_creation_guard import shell_commands
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

_SEPARATORS = re.compile(r"&&|\|\||[;|\n]")
_PUSH = re.compile(r"\bgit\b.*\bpush\b", re.DOTALL)


class AskRequesterGuardMiddleware(OpenSWEMiddleware):
    """Add an Ask run's requester to its pull requests before the run pushes to them."""

    state_schema = AgentState

    @staticmethod
    def _pushes(request: ToolCallRequest) -> bool:
        # Over-matching only excludes more people, so a loose match is the safe side.
        return any(_PUSH.search(command) for command in shell_commands(request))

    @staticmethod
    def _config(request: ToolCallRequest) -> RunnableConfig:
        config = getattr(getattr(request, "runtime", None), "config", None)
        return cast(RunnableConfig, config) if isinstance(config, Mapping) else get_config()

    @staticmethod
    def _pushed(request: ToolCallRequest, pulls: list[PullRequest]) -> list[PullRequest]:
        """The pull requests whose branches the pushes name.

        Each push in the command is read on its own; when any of them names no branch of an
        open pull request, or can't be read, every open pull request in the thread counts,
        which only ever refuses more approvals.
        """
        heads: dict[str, list[PullRequest]] = {}
        for pull in pulls:
            heads.setdefault(pull.head_ref, []).append(pull)
        picked: dict[str, list[PullRequest]] = {}
        for command in shell_commands(request):
            for segment in _SEPARATORS.split(command):
                if not _PUSH.search(segment):
                    continue
                try:
                    words = shlex.split(segment)
                except ValueError:
                    return pulls
                named = {
                    word.rsplit(":", 1)[-1].removeprefix("refs/heads/").lstrip("+")
                    for word in words
                    if not word.startswith("-")
                } & heads.keys()
                if not named:
                    # A push Robin can't pin to a branch may push any of them.
                    return pulls
                picked |= {head: heads[head] for head in named}
        return [pull for group in picked.values() for pull in group] or pulls

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        raise NotImplementedError

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if self._pushes(request):
            config = self._config(request)
            cfg = RunConfig.from_config(config)
            if (
                cfg.thread_id
                and cfg.github_login
                and await GitHubCredentialScope.is_public_ask(config)
            ):
                try:
                    for pull in self._pushed(
                        request, await PullRequest.open_for_thread(cfg.thread_id)
                    ):
                        await PullRequestApproval.add_requesters(
                            pull.owner, pull.repo, pull.number, {cfg.github_login}
                        )
                except Exception:
                    logger.exception(
                        "Failed to record the Ask push requester",
                        extra={"thread_id": cfg.thread_id},
                    )
                    return ToolMessage(
                        content=json.dumps(
                            {
                                "status": "error",
                                "error_type": "AskRequesterUnrecorded",
                                "error": "Robin could not record who asked for this push, so "
                                "their approval would count. Nothing ran; try again.",
                            }
                        ),
                        tool_call_id=str(request.tool_call.get("id") or ""),
                        status="error",
                    )
        return await handler(request)
