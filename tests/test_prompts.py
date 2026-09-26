import pytest
from jinja2 import UndefinedError
from langchain_core.tools import StructuredTool

from agent.prompts import apply_tool_descriptions, load_prompt, prompt


def sample_tool(value: str) -> str:
    """Inline description."""
    return value


def test_prompt_requires_all_placeholders() -> None:
    with pytest.raises(UndefinedError):
        prompt("model-selection")


def test_prompt_prefers_the_jinja_template() -> None:
    assert "expedited review card" in prompt(
        "runs/baby-sit-ready", pr_url="P", head_sha="H", expedited=True
    )


def test_jinja_prompt_requires_all_variables() -> None:
    with pytest.raises(UndefinedError):
        prompt("runs/baby-sit-ready", pr_url="P", head_sha="H")


def test_static_prompt_rejects_variables() -> None:
    with pytest.raises(ValueError, match="does not accept variables without a Jinja template"):
        prompt("system/shared-base", unused="value")


def test_static_prompt_loads_without_variables() -> None:
    assert prompt("system/shared-base") == load_prompt("system/shared-base.md")


def test_load_prompt_rejects_paths_outside_resources() -> None:
    with pytest.raises(ValueError):
        load_prompt("../default_prompt.md")


def test_apply_tool_descriptions_preserves_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    original_doc = sample_tool.__doc__
    monkeypatch.setattr("agent.prompts.load_prompt", lambda _: "Resource description.")
    described = apply_tool_descriptions([sample_tool])

    assert described == [sample_tool]
    assert sample_tool.__doc__ == "Resource description."
    sample_tool.__doc__ = original_doc


def test_apply_tool_descriptions_substitutes_values() -> None:
    source = StructuredTool.from_function(sample_tool, name="expose_port")
    [described] = apply_tool_descriptions(
        [source], {"expose_port": {"jwks_url": "https://keys.example"}}
    )

    assert "Verify it against `https://keys.example`" in described.description


def test_apply_tool_descriptions_copies_base_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    source = StructuredTool.from_function(sample_tool)
    monkeypatch.setattr("agent.prompts.load_prompt", lambda _: "Resource description.")

    [described] = apply_tool_descriptions([source])

    assert described is not source
    assert described.name == source.name
    assert described.args_schema == source.args_schema
    assert described.description == "Resource description."
