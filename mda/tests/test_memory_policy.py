from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from managed_deepagents import ManagedChannelContext
from memory import memory_is_internal
from rt.backend import Run, RunContext
from rt.file_tools import FileToolsMiddleware
from rt.memory_policy import MemoryPolicy
from runtime_api.catalog import Catalog, FilesSpec, MemoryScope

DM = MemoryScope(thread="other")
PUBLIC = MemoryScope(thread="public")
EXTERNAL = MemoryScope(thread="external")
OWN = "/memories/user/AGENTS.md"
ROBINS = "/memories/agent/AGENTS.md"


def _allows(scope: MemoryScope, name: str, arguments: Mapping[str, object]) -> bool:
    return MemoryPolicy(scope).refusal(name, arguments) is None


def _saves(scope: MemoryScope, path: str) -> bool:
    return _allows(scope, "write_file", {"file_path": path, "content": "x"})


def _reads(scope: MemoryScope, path: str) -> bool:
    return _allows(scope, "read_file", {"file_path": path})


@pytest.mark.parametrize("scope", [DM, PUBLIC])
def test_a_persons_own_memory_is_read_and_saved_anywhere_inside_the_company(
    scope: MemoryScope,
) -> None:
    for path in (OWN, "/memories/user/projects/robin.md"):
        assert _reads(scope, path) and _saves(scope, path)


def test_robins_memory_is_read_anywhere_but_only_saved_from_a_public_channel() -> None:
    for path in (ROBINS, "/memories/agent/projects/robin.md"):
        assert _saves(PUBLIC, path)
        assert _reads(DM, path) and not _saves(DM, path)
    edit = {"file_path": ROBINS, "old_string": "a", "new_string": "b"}
    assert _allows(PUBLIC, "edit_file", edit) and not _allows(DM, "edit_file", edit)


@pytest.mark.parametrize(
    "path", ["/memories/AGENTS.md", "/memories/agent/../team.md", "memories/other/x.md"]
)
def test_nothing_is_saved_outside_the_two_memories(path: str) -> None:
    assert not _saves(PUBLIC, path)


def test_a_run_the_backend_gave_no_scope_uses_no_memory() -> None:
    assert MemoryPolicy(None).refusal("read_file", {"file_path": ROBINS}) is not None


def test_memory_isnt_read_or_saved_where_outsiders_may_see() -> None:
    assert not _reads(EXTERNAL, OWN) and not _saves(EXTERNAL, OWN)
    for arguments in ({"pattern": "x"}, {"pattern": "x", "path": "/"}):
        assert not _allows(EXTERNAL, "grep", arguments)
        assert _allows(DM, "grep", arguments)
    assert _allows(EXTERNAL, "glob", {"pattern": "*.py", "path": "/workspace"})


def test_no_memory_file_is_ever_deleted() -> None:
    assert not _allows(PUBLIC, "delete", {"file_path": ROBINS})


@pytest.mark.parametrize(("scope", "mounted"), [(DM, True), (PUBLIC, True), (EXTERNAL, False)])
def test_memory_is_only_mounted_where_only_people_in_the_company_can_see_the_run(
    scope: MemoryScope, mounted: bool
) -> None:
    catalog = Catalog(memory=scope).model_dump(mode="json")

    assert memory_is_internal(RunContext.model_validate({"__catalog": catalog})) is mounted
    assert memory_is_internal(RunContext()) is False


def test_a_run_mdas_channel_delivered_mounts_for_the_policy_to_check() -> None:
    channel = {
        "name": "slack",
        "provider": "slack",
        "sourceThreadKey": "C1:1.0",
        "deliveryId": "d-1",
        "transport": "direct",
        "target": {},
    }

    assert memory_is_internal(ManagedChannelContext.model_validate({"channel": channel}))


@pytest.mark.parametrize("code_mode", [False, True])
async def test_the_file_tools_refuse_before_mda_sees_the_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code_mode: bool
) -> None:
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from managed_deepagents import ManagedToolRuntime

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools", "rt.file_tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    catalog = Catalog(files=FilesSpec(), memory=DM)
    context = RunContext.model_validate(
        {"__run_token": "token", "__catalog": catalog.model_dump(mode="json")}
    )
    backend = Run(context).backend
    monkeypatch.setattr(backend.outbox, "started", MagicMock())
    monkeypatch.setattr(backend.outbox, "finished", MagicMock())
    filesystem = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    tools = {tool.name: tool for tool in FilesystemMiddleware(backend=filesystem).tools}
    files = FileToolsMiddleware(backend, 300)
    model_request = MagicMock(spec=ModelRequest)
    model_request.tools = list(tools.values())
    await files.awrap_model_call(model_request, AsyncMock())
    runtime = ManagedToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
        backend=filesystem,  # ty: ignore[invalid-argument-type] - a stand-in for the sandbox
    )
    results: dict[str, ToolMessage] = {}
    for path in (OWN, ROBINS):
        args = {"file_path": path, "content": "# Saved\n"}
        if code_mode:
            [code_tool] = files.code_tools(["write_file"])
            result = await code_tool.ainvoke({**args, "runtime": runtime})
        else:
            call = ToolCall(name="write_file", args=args, id="call-1", type="tool_call")
            request = ToolCallRequest(
                tool_call=call,
                tool=tools["write_file"],
                state={},
                runtime=runtime,
            )
            result = await files.awrap_tool_call(request, files._run_own)
        assert isinstance(result, ToolMessage)
        results[path] = result

    assert results[OWN].status != "error"
    assert results[ROBINS].status == "error"
    assert (tmp_path / "memories/user/AGENTS.md").read_text() == "# Saved\n"
    assert not (tmp_path / "memories/agent/AGENTS.md").exists()
