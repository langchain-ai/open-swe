from openswe.dashboard.workspace_settings import (
    REVIEW_SCOUT_FALLBACK_MODEL,
    WorkspaceSettings,
)

_ROUTING_PAIRS = {
    "fast": ("google_genai:gemini-3.8-flash", "low"),
    "balanced": ("openai:gpt-6.1-sol", "medium"),
    "performance": ("anthropic:claude-opus-5-5", "high"),
}


def test_review_scout_falls_back_without_a_usable_balanced_tier() -> None:
    settings = WorkspaceSettings(
        {
            "default_agent_routing_balanced_model": "bogus:model",
            "default_agent_routing_balanced_reasoning_effort": "low",
        }
    )
    assert settings.review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL
