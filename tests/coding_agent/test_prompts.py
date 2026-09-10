from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from coding_agent.prompts import apply_tool_descriptions, load_prompt, render_prompt


def sample_tool(value: str) -> str:
    """Inline description."""
    return value


def test_render_prompt_requires_all_placeholders() -> None:
    with pytest.raises(KeyError):
        render_prompt("model-selection.md")


def test_load_prompt_rejects_paths_outside_resources() -> None:
    with pytest.raises(ValueError):
        load_prompt("../default_prompt.md")


def test_apply_tool_descriptions_preserves_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    original_doc = sample_tool.__doc__
    monkeypatch.setattr("coding_agent.prompts.load_prompt", lambda _: "Resource description.")
    described = apply_tool_descriptions([sample_tool])

    assert described == [sample_tool]
    assert sample_tool.__doc__ == "Resource description."
    sample_tool.__doc__ = original_doc


def test_apply_tool_descriptions_copies_base_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    source = StructuredTool.from_function(sample_tool)
    monkeypatch.setattr("coding_agent.prompts.load_prompt", lambda _: "Resource description.")

    [described] = apply_tool_descriptions([source])

    assert described is not source
    assert described.name == source.name
    assert described.args_schema == source.args_schema
    assert described.description == "Resource description."


def test_prompt_resources_are_markdown_files() -> None:
    root = Path(__file__).resolve().parents[2] / "coding_agent" / "resources" / "prompts"

    assert root.is_dir()
    assert all(path.suffix == ".md" for path in root.rglob("*") if path.is_file())
