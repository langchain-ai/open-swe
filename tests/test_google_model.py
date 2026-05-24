from agent.utils.model import (
    google_thinking_level_for,
    is_gemini_3_family,
    provider_model_kwargs,
)


def test_gemini_3_family_detection() -> None:
    assert is_gemini_3_family("google_genai:gemini-3.5-flash") is True
    assert is_gemini_3_family("google_genai:gemini-2.5-flash") is False


def test_google_thinking_level_maps_effort() -> None:
    assert google_thinking_level_for("medium") == "medium"
    assert google_thinking_level_for("high") == "high"
    assert google_thinking_level_for("unknown") is None


def test_provider_model_kwargs_for_google() -> None:
    kwargs = provider_model_kwargs(
        "google_genai:gemini-3.5-flash",
        "high",
        max_tokens=16_000,
    )
    assert kwargs["max_tokens"] == 16_000
    assert kwargs["thinking_level"] == "high"
