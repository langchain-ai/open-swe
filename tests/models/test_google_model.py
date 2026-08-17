from agent.dashboard.options import SUPPORTED_MODELS, provider_fallback_pair
from agent.utils.model import (
    google_thinking_level_for,
    is_gemini_3_family,
    provider_model_kwargs,
)

GEMINI_ID = "google_genai:gemini-3.7-flash"


def test_gemini_3_family_detection() -> None:
    assert is_gemini_3_family(GEMINI_ID) is True
    assert is_gemini_3_family("google_genai:gemini-2.5-flash") is False


def test_google_thinking_level_maps_effort() -> None:
    assert google_thinking_level_for("minimal") == "minimal"
    assert google_thinking_level_for("none") == "minimal"
    assert google_thinking_level_for("medium") == "medium"
    assert google_thinking_level_for("high") == "high"
    assert google_thinking_level_for("unknown") is None


def test_gemini_37_flash_is_supported_with_documented_efforts() -> None:
    gemini = next(m for m in SUPPORTED_MODELS if m["id"] == GEMINI_ID)
    assert gemini["label"] == "Gemini 3.7 Flash"
    assert gemini["efforts"] == ["minimal", "low", "medium", "high"]
    assert gemini["default_effort"] == "medium"


def test_gemini_35_flash_is_no_longer_offered() -> None:
    assert all(m["id"] != "google_genai:gemini-3.5-flash" for m in SUPPORTED_MODELS)


def test_renamed_gemini_35_flash_migrates_to_gemini_37_flash() -> None:
    assert provider_fallback_pair("google_genai:gemini-3.5-flash", "low") == (
        GEMINI_ID,
        "low",
    )


def test_google_provider_fallback_uses_gemini_37_flash() -> None:
    assert provider_fallback_pair("google_genai:gemini-3-flash-preview", "high") == (
        GEMINI_ID,
        "high",
    )


def test_google_provider_fallback_maps_legacy_none_to_minimal() -> None:
    assert provider_fallback_pair("google_genai:gemini-3-flash-preview", "none") == (
        GEMINI_ID,
        "minimal",
    )


def test_provider_model_kwargs_for_google() -> None:
    kwargs = provider_model_kwargs(
        GEMINI_ID,
        "high",
        max_tokens=16_000,
    )
    assert kwargs.get("max_tokens") == 16_000
    assert kwargs.get("thinking_level") == "high"
