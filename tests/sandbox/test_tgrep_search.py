import json
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse

from agent.sandboxes.tgrep_search import (
    build_tgrep_command,
    build_tgrep_server_command,
    parse_tgrep_result,
    warm_tgrep_server,
)

_START = "__OPEN_SWE_TGREP_START__"
_END = "__OPEN_SWE_TGREP_END__"


def test_tgrep_command_shell_quotes_inputs_and_requires_server() -> None:
    command = build_tgrep_command("$(touch /tmp/pwned)", "/workspace/repo", "*.py'; exit 1")

    assert "'$(touch /tmp/pwned)'" in command
    assert "'*.py'\"'\"'; exit 1'" in command
    assert 'test -f "$index/serve.json"' in command


def test_tgrep_server_command_is_idempotent_and_uses_watcher() -> None:
    command = build_tgrep_server_command("/workspace/repo; touch /tmp/pwned")

    assert "'/workspace/repo; touch /tmp/pwned'" in command
    assert "tgrep status" in command
    assert "nohup /usr/local/bin/tgrep serve" in command
    assert "--no-watch" not in command


@pytest.mark.asyncio
async def test_warm_tgrep_server_executes_startup_command() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = ExecuteResponse(output="", exit_code=0)

    await warm_tgrep_server(backend, "/workspace/repo")

    backend.aexecute.assert_awaited_once_with(
        build_tgrep_server_command("/workspace/repo"), timeout=15
    )


def test_parse_tgrep_result() -> None:
    match = {
        "type": "match",
        "data": {
            "path": {"text": "/workspace/repo/a.py"},
            "line_number": 2,
            "lines": {"text": "needle\n"},
        },
    }

    result = parse_tgrep_result(f"{_START}\n{json.dumps(match)}\n{json.dumps(match)}\n{_END}", 1)

    assert result is not None
    assert result.matches == [{"path": "/workspace/repo/a.py", "line": 2, "text": "needle"}]
    assert result.truncated is True


def test_parse_tgrep_result_falls_back_on_incomplete_or_malformed_output() -> None:
    assert parse_tgrep_result(f"{_START}\n") is None
    assert parse_tgrep_result(f"{_START}\nnot a match\n{_END}") is None
