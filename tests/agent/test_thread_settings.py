from openswe.utils.thread_settings import normalize_thread_settings


def test_normalize_thread_settings_rejects_invalid_values() -> None:
    settings, changed = normalize_thread_settings({"model_id": 1, "effort": "high"})

    assert settings == {}
    assert changed is True


def test_unrecognized_thread_models_are_dropped_to_fall_back_to_default() -> None:
    settings, changed = normalize_thread_settings(
        {
            "model_id": "anthropic:claude-fable-5-1",
            "effort": "high",
            "subagent_model_id": "openai:gpt-6-astra",
            "subagent_effort": "low",
            "requested_model": "unknown:model",
            "model_routing_enabled": False,
            "routing_models": {
                "fast": {"model_id": "google_genai:gemini-3.8-flash", "effort": "medium"},
                "balanced": {"model_id": "openai:gpt-6.1-sol", "effort": "medium"},
            },
        }
    )

    assert changed is True
    assert "model_id" not in settings
    assert "effort" not in settings
    assert "subagent_model_id" not in settings
    assert "subagent_effort" not in settings
    assert settings["requested_model"] is None
    assert settings["routing_models"] == {
        "balanced": {"model_id": "openai:gpt-6.1-sol", "effort": "medium"},
    }
    assert settings["model_routing_enabled"] is False
    assert normalize_thread_settings(settings) == (settings, False)
