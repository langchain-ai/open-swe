import json
from typing import Any

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent.middleware import external_repo_write_guard as guard


class _Runtime:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


class _Request:
    def __init__(self, name: str, args: dict[str, Any], config: dict[str, Any]) -> None:
        self.runtime = _Runtime(config)
        self.tool_call = {"name": name, "args": args, "id": "call-1"}


@pytest.fixture
def config() -> dict[str, Any]:
    return {"configurable": {"thread_id": "thread-1", "repo": {"owner": "MyOrg", "name": "target"}}}


async def _handler(_request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(content="called", tool_call_id="call-1")


@pytest.mark.parametrize(
    "command",
    [
        "gh issue create --repo OtherOrg/repo --title x",
        "bash -c 'gh issue create --repo OtherOrg/repo --title x'",
        "gh api --method PATCH repos/OtherOrg/repo/issues/1 --field title=x",
        "gh api repos/OtherOrg/repo/issues -f title=x",
        "curl -X POST https://api.github.com/repos/OtherOrg/repo/issues -d title=x",
    ],
)
async def test_external_shell_writes_are_blocked(config: dict[str, Any], command: str) -> None:
    request = _Request("execute", {"command": command}, config)
    result = await guard.ExternalRepoWriteGuardMiddleware().awrap_tool_call(request, _handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["error_type"] == "ExternalRepoWriteBlocked"
    assert payload["target_repo"] == "OtherOrg/repo"


async def test_external_http_write_is_blocked(config: dict[str, Any]) -> None:
    request = _Request(
        "http_request",
        {"url": "https://api.github.com/repos/OtherOrg/repo/issues", "method": "POST"},
        config,
    )
    result = await guard.ExternalRepoWriteGuardMiddleware().awrap_tool_call(request, _handler)
    assert isinstance(result, ToolMessage)
    assert json.loads(str(result.content))["error_type"] == "ExternalRepoWriteBlocked"


async def test_allowed_owner_non_target_requires_approval(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config["configurable"]["approved_repo_owners"] = ["OtherOrg"]
    pending: dict[str, Any] = {}

    async def fake_approved(_thread_id: str, _fingerprint: str) -> bool:
        return False

    async def fake_pending(_thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        pending.update(kwargs)
        return {"status": "pending"}, True

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)
    monkeypatch.setattr(guard, "ensure_workflow_push_pending", fake_pending)
    result = await guard.ExternalRepoWriteGuardMiddleware().awrap_tool_call(
        _Request("execute", {"command": "gh issue create --repo OtherOrg/repo"}, config), _handler
    )
    payload = json.loads(str(result.content))
    assert payload["approval_required"] is True
    assert pending["operation_kind"] == "gh issue create"


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda config: _Request(
            "execute", {"command": "gh issue comment --repo MyOrg/target 1 --body x"}, config
        ),
        lambda config: _Request(
            "execute", {"command": "gh issue view --repo OtherOrg/repo 1"}, config
        ),
        lambda config: _Request(
            "execute", {"command": "gh api --method GET repos/OtherOrg/repo/issues"}, config
        ),
    ],
)
async def test_same_repo_and_reads_pass_through(
    config: dict[str, Any], request_factory: Any
) -> None:
    result = await guard.ExternalRepoWriteGuardMiddleware().awrap_tool_call(
        request_factory(config), _handler
    )
    assert result.content == "called"
