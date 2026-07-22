from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from deepagents.middleware.skills import SkillsMiddleware
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from agent.middleware.trusted_skills import (
    TrustedSkillsMiddleware,
    TrustedSkillsState,
    _merge_trusted_skills_ref,
)


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


def test_merge_trusted_skills_ref_last_write_wins() -> None:
    assert _merge_trusted_skills_ref("a" * 40, "b" * 40) == "b" * 40
    assert _merge_trusted_skills_ref("a" * 40, None) == "a" * 40
    assert _merge_trusted_skills_ref(None, "b" * 40) == "b" * 40


async def test_trusted_skills_ref_merges_concurrent_updates() -> None:
    """Two concurrent writes to trusted_skills_ref in one step must merge, not raise."""
    ref = "c" * 40

    def fan_out(_state: TrustedSkillsState) -> list[Send]:
        return [Send("writer", {"n": 1}), Send("writer", {"n": 2})]

    def writer(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"trusted_skills_ref": ref}

    graph = (
        StateGraph(TrustedSkillsState)
        .add_node("writer", writer)
        .add_conditional_edges(START, fan_out, ["writer"])
        .add_edge("writer", END)
        .compile()
    )

    result = await graph.ainvoke({"messages": []})

    assert result["trusted_skills_ref"] == ref
