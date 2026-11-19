from openswe.dashboard.settings import (
    REVIEW_SCOUT_FALLBACK_MODEL,
    Settings,
)


def test_review_scout_falls_back_without_a_usable_balanced_tier() -> None:
    settings = Settings(
        {
            "default_agent_routing_balanced_model": "bogus:model",
            "default_agent_routing_balanced_reasoning_effort": "low",
        }
    )
    assert settings.review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL
