import pytest

from openswe.utils.thread_settings import normalize_thread_settings


def test_normalize_thread_settings_rejects_invalid_values() -> None:
    settings, changed = normalize_thread_settings({"model_id": 1, "effort": "high"})

    assert settings == {}
    assert changed is True


@pytest.mark.parametrize(
    ("effort", "expected_effort"), [("medium", "medium"), ("minimal", "high"), (None, "high")]
)
def test_retired_thread_models_use_the_successor(effort: str | None, expected_effort: str) -> None:
    settings, changed = normalize_thread_settings(
        {
            "model_id": "google_genai:gemini-3.8-flash",
            "effort": effort,
            "subagent_model_id": "google_genai:gemini-3.8-flash",
            "subagent_effort": effort,
            "requested_model": "google_genai:gemini-3.8-flash",
            "model_routing_enabled": False,
            "routing_models": {
                "fast": {"model_id": "google_genai:gemini-3.8-flash", "effort": effort},
                "balanced": {"model_id": "openai:gpt-6.1-sol", "effort": "medium"},
            },
        }
    )

    assert changed is True
    assert (settings["model_id"], settings["effort"]) == (
        "google_genai:gemini-4-hydrogen",
        expected_effort,
    )
    assert (settings["subagent_model_id"], settings["subagent_effort"]) == (
        "google_genai:gemini-4-hydrogen",
        expected_effort,
    )
    assert settings["requested_model"] == "google_genai:gemini-4-hydrogen"
    assert settings["routing_models"] == {
        "fast": {"model_id": "google_genai:gemini-4-hydrogen", "effort": expected_effort},
        "balanced": {"model_id": "openai:gpt-6.1-sol", "effort": "medium"},
    }
    assert settings["model_routing_enabled"] is False
    assert normalize_thread_settings(settings) == (settings, False)
