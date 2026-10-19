import pytest
from jinja2 import UndefinedError
from langchain_core.tools import StructuredTool

from openswe.prompts import (
    _deployment_context,
    apply_tool_descriptions,
    common_prompt,
    prompt,
)


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
        ({"OPENSWE_ENV": "STAGING"}, ("staging", "OPENSWE_ENV")),
        ({"OPENSWE_ENV": "prod"}, ("production", "OPENSWE_ENV")),
        ({"OPENSWE_ENV": "production"}, ("production", "OPENSWE_ENV")),
        ({"OPENSWE_ENV": "development"}, ("development", "OPENSWE_ENV")),
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
    if expected[0] in {"preview", "staging"}:
        assert f"**{expected[0]}**" in common_prompt()
        assert common_prompt("existing prompt").endswith("\n\nexisting prompt")
    else:
        assert common_prompt() == ""
        assert common_prompt("existing prompt") == "existing prompt"


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


def test_described_function_tools_are_reused_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reuse keeps each tool's memoized model-facing schema from being rebuilt every run.
    monkeypatch.setattr("openswe.prompts._load_prompt", lambda _: "Resource description.")

    [first] = apply_tool_descriptions([sample_tool])
    [second] = apply_tool_descriptions([sample_tool])

    assert first is second
    assert first.description == "Resource description."
