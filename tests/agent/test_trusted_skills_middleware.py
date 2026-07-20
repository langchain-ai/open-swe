from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from deepagents.middleware.skills import SkillsMiddleware
from langgraph.runtime import Runtime

from agent.middleware.trusted_skills import TrustedSkillsMiddleware, TrustedSkillsState


async def test_trusted_skills_reload_when_ref_changes() -> None:
    middleware = TrustedSkillsMiddleware(
        backend=MagicMock(),
        sources=["/work/.agent-skills/acme/widget/.agents/skills/"],
        trusted_ref="b" * 40,
    )
    state: TrustedSkillsState = {
        "messages": [],
        "skills_metadata": [MagicMock()],
        "skills_load_errors": ["old error"],
        "trusted_skills_ref": "a" * 40,
    }

    with patch.object(
        SkillsMiddleware,
        "abefore_agent",
        new_callable=AsyncMock,
        return_value={"skills_metadata": [], "skills_load_errors": []},
    ) as load:
        update = await middleware.abefore_agent(state, MagicMock(spec=Runtime), {})

    assert update == {
        "skills_metadata": [],
        "skills_load_errors": [],
        "trusted_skills_ref": "b" * 40,
    }
    assert load.await_args is not None
    reloaded_state = load.await_args.args[0]
    assert "skills_metadata" not in reloaded_state
    assert "skills_load_errors" not in reloaded_state


async def test_trusted_skills_reuse_metadata_for_unchanged_ref() -> None:
    trusted_ref = "a" * 40
    middleware = TrustedSkillsMiddleware(
        backend=MagicMock(),
        sources=["/work/.agent-skills/acme/widget/.agents/skills/"],
        trusted_ref=trusted_ref,
    )
    state: TrustedSkillsState = {
        "messages": [],
        "skills_metadata": [],
        "trusted_skills_ref": trusted_ref,
    }

    with patch.object(
        SkillsMiddleware,
        "abefore_agent",
        new_callable=AsyncMock,
    ) as load:
        update = await middleware.abefore_agent(state, MagicMock(spec=Runtime), {})

    assert update is None
    load.assert_not_awaited()
