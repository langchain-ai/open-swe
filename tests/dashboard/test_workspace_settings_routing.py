from agent.dashboard.workspace_settings import (
    REVIEW_SCOUT_FALLBACK_MODEL,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)

_ROUTING_PAIRS = {
    "fast": ("google_genai:gemini-3.8-flash", "low"),
    "balanced": ("openai:gpt-6-sol", "medium"),
    "performance": ("anthropic:claude-opus-5-5", "high"),
}


def _routing_settings() -> dict[str, object]:
    return {
        f"default_agent_routing_{tier}_{suffix}": value
        for tier, pair in _ROUTING_PAIRS.items()
        for suffix, value in zip(("model", "reasoning_effort"), pair, strict=True)
    }


async def test_agent_routing_uses_configured_model_pairs() -> None:
    assert WorkspaceSettings(_routing_settings()).agent_routing_models == _ROUTING_PAIRS


def test_workspace_settings_update_accepts_routing_pairs() -> None:
    update = WorkspaceSettingsUpdate(**_routing_settings())
    assert update.default_agent_routing_fast_model == _ROUTING_PAIRS["fast"][0]
    assert (
        update.default_agent_routing_performance_reasoning_effort
        == _ROUTING_PAIRS["performance"][1]
    )


def test_review_scout_uses_the_balanced_routing_tier() -> None:
    assert WorkspaceSettings(_routing_settings()).review_scout_model == _ROUTING_PAIRS["balanced"]


def test_review_scout_falls_back_without_a_usable_balanced_tier() -> None:
    settings = WorkspaceSettings(
        {
            "default_agent_routing_balanced_model": "bogus:model",
            "default_agent_routing_balanced_reasoning_effort": "low",
        }
    )
    assert settings.review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL
