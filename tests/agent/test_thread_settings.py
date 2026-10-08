from openswe.utils.thread_settings import normalize_thread_settings


def test_normalize_thread_settings_rejects_invalid_values() -> None:
    settings, changed = normalize_thread_settings({"model_id": 1, "effort": "high"})

    assert settings == {}
    assert changed is True
