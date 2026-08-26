from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ReadResult

from agent.server import _resolve_repo_skill_sources
from agent.utils.skill_backend import SkillCompositeBackend


@pytest.mark.asyncio
async def test_resolve_repo_skill_sources_is_best_effort() -> None:
    backend = MagicMock()
    backend.aexecute = AsyncMock(side_effect=[MagicMock(exit_code=0), RuntimeError("probe failed")])
    with patch(
        "agent.server.aresolve_sandbox_work_dir", new_callable=AsyncMock, return_value="/work"
    ):
        sources = await _resolve_repo_skill_sources(
            backend,
            {"repo": {"owner": "acme", "name": "widget"}},
        )

    assert sources == ["/work/widget/.agents/skills/"]


@pytest.mark.asyncio
async def test_skill_route_read_error_preserves_requested_path() -> None:
    backend = MagicMock()
    backend.aread = AsyncMock(
        return_value=ReadResult(error="File '/pr-creation/SKILL.md' not found")
    )
    composite = SkillCompositeBackend(
        default=backend,
        routes={"/organization-skills/": backend, "/bundled-skills/": backend},
    )

    result = await composite.aread("/organization-skills/pr-creation/SKILL.md")

    assert result.error is not None
    assert "/organization-skills/pr-creation/SKILL.md" in result.error
    assert "/organization-skills/" in result.error
    assert "/bundled-skills/" in result.error
