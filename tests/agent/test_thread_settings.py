from agent.utils.thread_settings import normalize_thread_settings


def test_normalize_thread_settings_removes_unknown_keys() -> None:
    settings, changed = normalize_thread_settings({"model_id": "model", "draft_prs": True})

    assert settings == {"model_id": "model"}
    assert changed is True


def test_normalize_thread_settings_rejects_invalid_values() -> None:
    settings, changed = normalize_thread_settings({"model_id": 1, "effort": "high"})

    assert settings == {}
    assert changed is True


def test_normalize_thread_settings_preserves_valid_values() -> None:
    original = {
        "model_id": "model",
        "effort": None,
        "subagent_model_id": "subagent",
        "subagent_effort": "high",
        "adaptive_model_routing": True,
        "repo_instructions": None,
    }

    settings, changed = normalize_thread_settings(original)

    assert settings == original
    assert changed is False
