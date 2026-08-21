from agent.prompt import construct_system_prompt


def test_prompt_requires_screenshots_for_ui_changes() -> None:
    prompt = construct_system_prompt(working_dir="/workspace")

    assert "Take a screenshot for applicable UI-facing changes" in prompt
