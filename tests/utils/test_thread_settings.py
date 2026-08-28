from agent.utils.thread_settings import normalize_thread_settings


def test_deprecated_model_resets_thread_to_team_defaults() -> None:
    settings, changed = normalize_thread_settings(
        {
            "model_id": "fireworks:accounts/fireworks/models/glm-5p2",
            "effort": "high",
            "subagent_model_id": "fireworks:accounts/fireworks/models/glm-5p3",
            "subagent_effort": "high",
            "repo_instructions": "keep",
        }
    )

    assert settings == {"repo_instructions": "keep"}
    assert changed is True
