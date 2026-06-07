import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langgraph.graph.state import RunnableConfig

from agent.utils.codegraph import (
    ensure_codegraph_installed,
    ensure_codegraph_indexed,
    get_affected_tests_via_codegraph,
    get_workspace_files_via_codegraph,
)
from agent.tools.codegraph_tools import (
    codegraph_search,
    codegraph_callers,
    codegraph_callees,
    codegraph_impact,
)


@pytest.fixture
def mock_sandbox():
    sandbox = MagicMock()
    return sandbox


def test_ensure_codegraph_installed_already_present(mock_sandbox):
    # codegraph --version succeeds
    mock_sandbox.execute.return_value = MagicMock(exit_code=0, output="0.9.9")
    assert ensure_codegraph_installed(mock_sandbox) is True
    mock_sandbox.execute.assert_called_once_with("codegraph --version")


def test_ensure_codegraph_installed_trigger_npm_install(mock_sandbox):
    # codegraph --version fails first, then npm install succeeds
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=127, output="command not found"),
        MagicMock(exit_code=0, output="added 1 package"),
    ]
    assert ensure_codegraph_installed(mock_sandbox) is True
    assert mock_sandbox.execute.call_count == 2


def test_ensure_codegraph_indexed_sync(mock_sandbox):
    # version check succeeds, db exists, sync succeeds
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
    ]
    assert ensure_codegraph_indexed(mock_sandbox, "/work") is True
    assert mock_sandbox.execute.call_count == 3


def test_ensure_codegraph_indexed_init(mock_sandbox):
    # version check succeeds, db doesn't exist, init & index succeeds
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="False"),  # exists check
        MagicMock(exit_code=0, output="indexed"),  # init & index
    ]
    assert ensure_codegraph_indexed(mock_sandbox, "/work") is True


def test_get_affected_tests(mock_sandbox):
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="tests/test_calc.py\n"),  # affected
    ]
    res = get_affected_tests_via_codegraph(mock_sandbox, "/work")
    assert res == ["tests/test_calc.py"]


def test_get_workspace_files(mock_sandbox):
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="src\\index.ts\nREADME.md\n"),  # files
    ]
    res = get_workspace_files_via_codegraph(mock_sandbox, "/work")
    assert res == ["src/index.ts", "README.md"]


@pytest.mark.asyncio
async def test_codegraph_tools_search(mock_sandbox):
    # Mock database and path resolution
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="User class"),  # query result
    ]

    config: RunnableConfig = {"configurable": {"thread_id": "thread-123"}}

    with (
        patch(
            "agent.tools.codegraph_tools.get_sandbox_backend", AsyncMock(return_value=mock_sandbox)
        ),
        patch(
            "agent.tools.codegraph_tools.aresolve_sandbox_work_dir",
            AsyncMock(return_value="/work"),
        ),
    ):
        # Invoke tool using .ainvoke or calling it directly
        res = await codegraph_search.ainvoke({"query": "User"}, config)
        assert "User class" in res


@pytest.mark.asyncio
async def test_codegraph_tools_callers(mock_sandbox):
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="callers result"),  # query result
    ]

    config: RunnableConfig = {"configurable": {"thread_id": "thread-123"}}

    with (
        patch(
            "agent.tools.codegraph_tools.get_sandbox_backend", AsyncMock(return_value=mock_sandbox)
        ),
        patch(
            "agent.tools.codegraph_tools.aresolve_sandbox_work_dir",
            AsyncMock(return_value="/work"),
        ),
    ):
        res = await codegraph_callers.ainvoke({"symbol": "login"}, config)
        assert "callers result" in res


@pytest.mark.asyncio
async def test_codegraph_tools_callees(mock_sandbox):
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="callees result"),  # query result
    ]

    config: RunnableConfig = {"configurable": {"thread_id": "thread-123"}}

    with (
        patch(
            "agent.tools.codegraph_tools.get_sandbox_backend", AsyncMock(return_value=mock_sandbox)
        ),
        patch(
            "agent.tools.codegraph_tools.aresolve_sandbox_work_dir",
            AsyncMock(return_value="/work"),
        ),
    ):
        res = await codegraph_callees.ainvoke({"symbol": "login"}, config)
        assert "callees result" in res


@pytest.mark.asyncio
async def test_codegraph_tools_impact(mock_sandbox):
    mock_sandbox.execute.side_effect = [
        MagicMock(exit_code=0, output="0.9.9"),  # version
        MagicMock(exit_code=0, output="True"),  # exists check
        MagicMock(exit_code=0, output="sync complete"),  # sync
        MagicMock(exit_code=0, output="impact result"),  # query result
    ]

    config: RunnableConfig = {"configurable": {"thread_id": "thread-123"}}

    with (
        patch(
            "agent.tools.codegraph_tools.get_sandbox_backend", AsyncMock(return_value=mock_sandbox)
        ),
        patch(
            "agent.tools.codegraph_tools.aresolve_sandbox_work_dir",
            AsyncMock(return_value="/work"),
        ),
    ):
        res = await codegraph_impact.ainvoke({"symbol": "login"}, config)
        assert "impact result" in res
