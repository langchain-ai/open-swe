from unittest.mock import AsyncMock, patch

import pytest

from openswe import reviewer
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.review_scout import graph as scout
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.search_policy import SHELL_SEARCH_EXCLUDED_TOOLS
from tests.agent.test_agent_assembly_context import _agent_catalog, _base_config
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope

_PLANNERS = ["main", "reviewer", "review-scout"]


async def _planned(kind: str) -> ToolSurface:
    if kind == "main":
        planned = (await _agent_catalog())["surface"]
        assert isinstance(planned, ToolSurface)
        return planned
    module = reviewer if kind == "reviewer" else scout
    plan = reviewer.reviewer_catalog if kind == "reviewer" else scout.review_scout_catalog
    surface = ToolSurface()
    with patch.object(
        module, "cached_workspace_settings", AsyncMock(return_value=WorkspaceSettings({}))
    ):
        await plan(_base_config(), surface)
    return surface


async def test_the_agent_applies_the_shared_search_policy(
    saved_thread_scope: dict[str, object],
) -> None:
    # The reviewer's and the scout's are MDA's, the same for each of their runs (``rt/kinds.py``).
    surface = await _planned("main")
    assert set(surface.loop.hidden_tools) >= SHELL_SEARCH_EXCLUDED_TOOLS


@pytest.mark.parametrize("kind", _PLANNERS)
async def test_a_planned_run_leaves_the_file_and_shell_tools_to_mda(
    kind: str, saved_thread_scope: dict[str, object]
) -> None:
    surface = await _planned(kind)

    await surface.prepare()

    # The file and shell tools are MDA's own.
    assert not {"ls", "read_file", "write_file", "edit_file", "execute"} & surface.tools.keys()
    assert {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in surface.base_tools
    } <= (surface.tools.keys() | SHELL_SEARCH_EXCLUDED_TOOLS)
