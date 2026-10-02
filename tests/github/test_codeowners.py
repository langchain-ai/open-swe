from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.github.codeowners import codeowners_for_files, login_is_codeowner


@pytest.mark.parametrize(
    ("content", "filenames", "expected"),
    [
        ("* @default\n*.py @python\napp.py @app", ["app.py"], {"@app"}),
        ("/smith-go/mcp/ @fleet", ["smith-go/mcp/server.go"], {"@fleet"}),
        ("/README @docs", ["docs/README"], set()),
        ("*.go @go\nsmith-go/mcp/ @fleet", ["smith-go/mcp/server.go"], {"@fleet"}),
        ("*.go @org/platform", ["internal/main.go"], {"@org/platform"}),
    ],
)
def test_codeowners_matching_uses_last_rule_and_directory_patterns(
    content: str, filenames: list[str], expected: set[str]
) -> None:
    assert codeowners_for_files(content, filenames) == expected


@pytest.mark.asyncio
async def test_team_member_is_accepted_as_codeowner() -> None:
    response = AsyncMock(return_value=httpx2.Response(200, json={"state": "active"}))
    with (
        patch(
            "agent.github.codeowners.codeowners_for_pull_request",
            AsyncMock(return_value={"@org/team"}),
        ),
        patch("agent.github.codeowners.github_request", response),
    ):
        accepted, owners, failed = await login_is_codeowner("o", "r", 1, "alice", "token")
    assert accepted
    assert owners == {"@org/team"}
    assert not failed


@pytest.mark.asyncio
async def test_team_lookup_failure_does_not_mark_lookup_as_definitive_refusal() -> None:
    response = AsyncMock(side_effect=httpx2.ConnectError("offline"))
    with (
        patch(
            "agent.github.codeowners.codeowners_for_pull_request",
            AsyncMock(return_value={"@org/team"}),
        ),
        patch("agent.github.codeowners.github_request", response),
    ):
        accepted, owners, failed = await login_is_codeowner("o", "r", 1, "alice", "token")
    assert not accepted
    assert owners == {"@org/team"}
    assert failed
