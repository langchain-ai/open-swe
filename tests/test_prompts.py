import pytest
from jinja2 import UndefinedError
from langchain_core.tools import StructuredTool

from agent.prompt import _deployment_context, construct_system_prompt
from agent.prompts import apply_tool_descriptions, load_prompt, prompt


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        (
            {"OPENSWE_ENV": " preview ", "LANGSMITH_LANGGRAPH_API_VARIANT": "local_dev"},
            ("preview", "OPENSWE_ENV"),
        ),
        (
            {"LANGSMITH_LANGGRAPH_API_VARIANT": "local_dev"},
            ("local", "LANGSMITH_LANGGRAPH_API_VARIANT"),
        ),
        ({}, ("unknown", "no explicit environment or local runtime marker")),
    ],
)
def test_deployment_context(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], expected: tuple[str, str]
) -> None:
    for name in (
        "OPENSWE_ENV",
        "LANGSMITH_LANGGRAPH_API_VARIANT",
        "LANGGRAPH_URL",
        "DASHBOARD_API_BASE_URL",
        "DASHBOARD_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)

    assert _deployment_context() == expected
    assert f"**{expected[0]}**" in construct_system_prompt("/root")

    monkeypatch.setenv("OPENSWE_ENV", "staging")
    assert "**staging**" in construct_system_prompt("/root")


def sample_tool(value: str) -> str:
    """Inline description."""
    return value


def test_jinja_prompt_requires_all_variables() -> None:
    with pytest.raises(UndefinedError):
        prompt("runs/baby-sit-ready", pr_url="P", head_sha="H")


def test_load_prompt_rejects_paths_outside_resources() -> None:
    with pytest.raises(ValueError):
        load_prompt("../default_prompt.md")


def test_apply_tool_descriptions_copies_base_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    source = StructuredTool.from_function(sample_tool)
    monkeypatch.setattr("agent.prompts.load_prompt", lambda _: "Resource description.")

    [described] = apply_tool_descriptions([source])

    assert described is not source
    assert described.name == source.name
    assert described.args_schema == source.args_schema
    assert described.description == "Resource description."
