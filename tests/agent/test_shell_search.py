from unittest.mock import AsyncMock, patch

import pytest

from openswe import reviewer
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.review_scout import graph as scout
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.search_policy import SHELL_SEARCH_EXCLUDED_TOOLS
from tests.agent.test_agent_assembly_context import _base_config, _plan_agent
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope

_PLANNERS = ["main", "reviewer", "review-scout"]


async def _planned(kind: str) -> ToolSurface:
    if kind == "main":
        planned = (await _plan_agent())["surface"]
        assert isinstance(planned, ToolSurface)
        return planned
    module = reviewer if kind == "reviewer" else scout
    plan = reviewer.plan_reviewer if kind == "reviewer" else scout.plan_review_scout
    surface = ToolSurface()
    with patch.object(
        module, "cached_workspace_settings", AsyncMock(return_value=WorkspaceSettings({}))
    ):
        await plan(_base_config(), surface)
    return surface


@pytest.mark.parametrize("kind", _PLANNERS)
async def test_shell_agent_planners_apply_shared_search_policy(
    kind: str, saved_thread_scope: dict[str, object]
) -> None:
    surface = await _planned(kind)
    assert set(surface.loop.hidden_tools) == SHELL_SEARCH_EXCLUDED_TOOLS


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
