import pytest
from jinja2 import UndefinedError
from langchain_core.tools import StructuredTool

from openswe.prompts import apply_tool_descriptions, prompt


def sample_tool(value: str) -> str:
    """Inline description."""
    return value


def test_jinja_prompt_requires_all_variables() -> None:
    with pytest.raises(UndefinedError):
        prompt("runs/baby-sit-ready", pr_url="P", head_sha="H")


def test_prompt_rejects_paths_outside_resources() -> None:
    with pytest.raises(ValueError):
        prompt("../default_prompt")


def test_apply_tool_descriptions_copies_base_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    source = StructuredTool.from_function(sample_tool)
    monkeypatch.setattr("openswe.prompts._load_prompt", lambda _: "Resource description.")

    [described] = apply_tool_descriptions([source])

    assert described is not source
    assert described.name == source.name
    assert described.args_schema == source.args_schema
    assert described.description == "Resource description."
