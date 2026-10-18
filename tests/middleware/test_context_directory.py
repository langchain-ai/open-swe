from typing import cast

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import JsonValue

from openswe.middleware.context_directory import ContextDirectoryMiddleware
from openswe.sandboxes.context_directory import ContextDirectory

_OPEN = {
    "context_directory": {
        "path": "/workspace/mono/packages/app",
        "repo": "acme/mono",
        "subpath": "packages/app",
    }
}


def _request(name: str, args: dict[str, JsonValue], state: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=None,
        state=state,
        runtime=cast(ToolRuntime, None),
    )


async def _run(
    name: str, args: dict[str, JsonValue], state: dict[str, object]
) -> tuple[ToolMessage | Command, dict[str, JsonValue] | None]:
    received: list[dict[str, JsonValue]] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        received.append(dict(request.tool_call["args"]))
        return ToolMessage(content="ok", tool_call_id="call-1")

    middleware = ContextDirectoryMiddleware(FilesystemMiddleware(backend=StateBackend()))
    result = await middleware.awrap_tool_call(_request(name, args, state), handler)
    return result, received[0] if received else None


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("edit_file", {"file_path": "/workspace/repo/a.py", "old_string": "a", "new_string": "b"}),
        ("write_file", {"file_path": "/workspace/repo/a.py", "content": ""}),
        ("delete", {"file_path": "/workspace/repo/a.py"}),
        ("read_file", {"file_path": "src/a.py"}),
    ],
)
async def test_edits_and_relative_paths_wait_for_an_open_directory(
    name: str, args: dict[str, JsonValue]
) -> None:
    result, received = await _run(name, args, {})

    assert received is None
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "open_directory" in str(result.content)


async def test_reads_and_commands_run_unchanged_before_a_directory_opens() -> None:
    _, read = await _run("read_file", {"file_path": "/workspace/repo/a.py"}, {})
    _, command = await _run("execute", {"command": "git status"}, {})

    assert read == {"file_path": "/workspace/repo/a.py"}
    assert command == {"command": "git status"}


async def test_open_directory_anchors_paths_and_commands() -> None:
    _, edit = await _run(
        "edit_file", {"file_path": "../lib/a.py", "old_string": "a", "new_string": "b"}, _OPEN
    )
    _, absolute = await _run("read_file", {"file_path": "/tmp/notes.md"}, _OPEN)
    _, search = await _run("grep", {"pattern": "TODO"}, _OPEN)
    _, command = await _run("execute", {"command": "make test || true"}, _OPEN)
    _, monitor = await _run(
        "monitor", {"action": "start", "command": "make watch", "filter": "grep FAIL"}, _OPEN
    )

    assert edit is not None and edit["file_path"] == "/workspace/mono/packages/lib/a.py"
    assert absolute == {"file_path": "/tmp/notes.md"}
    assert search == {"pattern": "TODO", "path": "/workspace/mono/packages/app"}
    assert command == {"command": "cd /workspace/mono/packages/app && {\nmake test || true\n}"}
    assert monitor == {
        "action": "start",
        "command": "cd /workspace/mono/packages/app && {\nmake watch\n}",
        "filter": "cd /workspace/mono/packages/app && {\ngrep FAIL\n}",
    }


class _Sandbox:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=self.output, exit_code=self.exit_code)


@pytest.mark.parametrize(
    ("output", "expected", "checkout"),
    [
        (
            "/workspace/mono/packages/app\n/workspace/mono\nhttps://github.com/acme/mono.git\n",
            ContextDirectory(
                path="/workspace/mono/packages/app", repo="acme/mono", subpath="packages/app"
            ),
            "/workspace/mono",
        ),
        (
            "/workspace/repo\n/workspace/repo\ngit@github.com:acme/repo.git\n",
            ContextDirectory(path="/workspace/repo", repo="acme/repo", subpath=""),
            "/workspace/repo",
        ),
        ("/tmp/scratch\n\n\n", ContextDirectory(path="/tmp/scratch"), None),
    ],
)
async def test_open_records_the_repository_and_subpath(
    output: str, expected: ContextDirectory, checkout: str | None
) -> None:
    directory = await ContextDirectory.open(
        cast(SandboxBackendProtocol, _Sandbox(output)), expected.path
    )

    assert directory == expected
    assert directory.checkout == checkout


async def test_open_refuses_a_missing_directory() -> None:
    sandbox = cast(
        SandboxBackendProtocol, _Sandbox("cd: /workspace/nope: No such file or directory", 1)
    )

    with pytest.raises(ValueError, match="not a directory"):
        await ContextDirectory.open(sandbox, "/workspace/nope")
