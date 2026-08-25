from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.corridor_commit_scan import CorridorCommitScanMiddleware


@pytest.fixture(autouse=True)
def enable_corridor_commit_scanning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")


def _request(command: str) -> MagicMock:
    request = MagicMock()
    request.tool_call = {
        "name": "execute",
        "id": "call-1",
        "args": {"command": command},
    }
    return request


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m test",
        "git commit -n -m test",
        "git -c core.hooksPath=/tmp/hooks commit -m test",
        "git config core.hooksPath /tmp/hooks",
        "git config --global core.hooksPath /tmp/hooks",
        "rm -rf /root/.config/open-swe/git-hooks",
    ],
)
async def test_blocks_commit_scan_bypass(command: str) -> None:
    handler = AsyncMock()

    result = await CorridorCommitScanMiddleware().awrap_tool_call(_request(command), handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    handler.assert_not_awaited()


async def test_allows_normal_git_commands() -> None:
    request = _request("git commit -m test")
    expected = ToolMessage(content="ok", tool_call_id="call-1")
    handler = AsyncMock(return_value=expected)

    result = await CorridorCommitScanMiddleware().awrap_tool_call(request, handler)

    assert result is expected
    handler.assert_awaited_once_with(request)


async def test_is_inactive_when_commit_scanning_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "false")
    request = _request("git commit --no-verify -m test")
    expected = ToolMessage(content="ok", tool_call_id="call-1")
    handler = AsyncMock(return_value=expected)

    result = await CorridorCommitScanMiddleware().awrap_tool_call(request, handler)

    assert result is expected
