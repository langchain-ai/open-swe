from __future__ import annotations

import json
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent.middleware.pr_creation_guard import (
    PullRequestCreationGuardMiddleware,
    is_pr_creation_fallback_command,
)


class _Request:
    def __init__(self, command: str) -> None:
        self.tool_call = {
            "name": "execute",
            "args": {"command": command},
            "id": "call-1",
        }


async def _handler(_request: Any) -> ToolMessage:
    return ToolMessage(content="allowed", tool_call_id="call-1")


def test_detects_pr_creation_fallback_commands() -> None:
    assert is_pr_creation_fallback_command("GH_TOKEN=dummy gh pr create --draft")
    assert is_pr_creation_fallback_command(
        "gh api repos/langchain-ai/open-swe/pulls -X POST -f title=x"
    )
    assert is_pr_creation_fallback_command(
        "gh api -X POST repos/langchain-ai/open-swe/pulls -f title=x"
    )
    assert is_pr_creation_fallback_command(
        "GH_TOKEN=dummy gh api -X POST repos/langchain-ai/open-swe/pulls -f title=x"
    )
    assert is_pr_creation_fallback_command(
        "curl -X POST https://api.github.com/repos/langchain-ai/open-swe/pulls -d '{}'"
    )
    assert is_pr_creation_fallback_command("/usr/bin/gh pr create --draft")
    assert is_pr_creation_fallback_command(
        "/usr/bin/curl -X POST https://api.github.com/repos/langchain-ai/open-swe/pulls -d '{}'"
    )
    assert is_pr_creation_fallback_command("bash -c 'gh pr create --draft'")
    assert is_pr_creation_fallback_command("GH_TOKEN=dummy sh -c 'gh pr create --draft'")
    assert is_pr_creation_fallback_command(
        "zsh -lc 'gh api repos/langchain-ai/open-swe/pulls -X POST -f title=x'"
    )
    assert is_pr_creation_fallback_command(
        "bash -c \"curl -X POST https://api.github.com/repos/langchain-ai/open-swe/pulls -d '{}'\""
    )
    assert is_pr_creation_fallback_command(
        'bash -c \'sh -c "dash -c \\"zsh -c \\\\\\"gh pr create --draft\\\\\\"\\""\''
    )


def test_allows_safe_pr_commands() -> None:
    assert not is_pr_creation_fallback_command("GH_TOKEN=dummy gh pr view 1 --json url")
    assert not is_pr_creation_fallback_command("gh pr list --head open-swe/foo")
    assert not is_pr_creation_fallback_command("gh pr edit 1 --add-label ready")
    assert not is_pr_creation_fallback_command("gh pr comment 1 --body done")
    assert not is_pr_creation_fallback_command("bash -c 'gh pr view 1 --json url'")
    assert not is_pr_creation_fallback_command("/usr/bin/gh pr view 1 --json url")


async def test_middleware_blocks_execute_pr_creation_fallbacks() -> None:
    for command in (
        "GH_TOKEN=dummy gh pr create --draft",
        "gh api repos/langchain-ai/open-swe/pulls -X POST -f title=x",
        "GH_TOKEN=dummy gh api -X POST repos/langchain-ai/open-swe/pulls -f title=x",
        "curl -X POST https://api.github.com/repos/langchain-ai/open-swe/pulls -d '{}'",
    ):
        result = await PullRequestCreationGuardMiddleware().awrap_tool_call(
            cast(ToolCallRequest, _Request(command)), _handler
        )

        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        payload = json.loads(str(result.content))
        assert payload["code"] == "pr_creation_fallback_blocked"
        assert payload["recoverable_by_agent"] is False
        assert "open_pull_request" in payload["error"]
        assert payload["blocked_command"] == command


async def test_middleware_allows_safe_pr_view() -> None:
    result = await PullRequestCreationGuardMiddleware().awrap_tool_call(
        cast(ToolCallRequest, _Request("GH_TOKEN=dummy gh pr view 1 --json url")), _handler
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "allowed"
