"""The rendered system prompt must name the real working directory.

``str.format`` does not re-scan the values it substitutes, so a section that is
itself substituted has to resolve its own ``{working_dir}`` first. When it does
not, the model is told to work in a directory literally called
``{working_dir}``.
"""

import pytest

from agent.prompt import construct_system_prompt


@pytest.mark.parametrize("local_workspace", [False, True])
def test_working_environment_section_names_the_working_directory(
    local_workspace: bool,
) -> None:
    prompt = construct_system_prompt(
        working_dir="/home/user/workspace", local_workspace=local_workspace
    )

    assert "`/home/user/workspace`" in prompt
    assert "{working_dir}" not in prompt


def test_repo_setup_section_names_the_working_directory() -> None:
    prompt = construct_system_prompt(working_dir="/sandbox/work")

    assert "cd /sandbox/work && gh repo clone <owner>/<repo>" in prompt
