from collections.abc import Callable, Mapping, Sequence
from functools import cache, lru_cache

from langchain_core.tools import BaseTool, tool
from openswe_shared.prompting import is_template, prompt

from openswe.config import ENV


@cache
def _function_tool(function: Callable[..., object]) -> BaseTool:
    """Infer a function's tool schema once per process.

    Graphs are assembled on every run, and a bare function there is converted
    again each time, which makes schema inference most of the assembly cost.
    """
    return tool(function, description=function.__doc__ or getattr(function, "__name__", "tool"))


@lru_cache(maxsize=1024)
def _described_function_tool(function: Callable[..., object], description: str) -> BaseTool:
    # A tool memoizes the schema it sends to the model per instance, so reusing
    # the described copy is what keeps every run's model calls from rebuilding it.
    return _function_tool(function).model_copy(update={"description": description})


def _description(
    name: str, values: Mapping[str, Mapping[str, object]] | None, *, mcp: bool
) -> str | None:
    template = f"tools/{name}"
    substitutions = (values or {}).get(name)
    try:
        if is_template(template):
            return prompt(template, substitutions, mcp=mcp)
        return prompt(template, substitutions)
    except FileNotFoundError:
        return None


def apply_tool_descriptions(
    tools: Sequence[BaseTool | Callable[..., object]],
    values: Mapping[str, Mapping[str, object]] | None = None,
    *,
    mcp: bool = False,
) -> list[BaseTool]:
    """Describe each tool from ``tools/<name>.md``, rendering templates with ``values`` and ``mcp``."""
    described: list[BaseTool] = []
    for value in tools:
        if isinstance(value, BaseTool):
            description = _description(value.name, values, mcp=mcp)
            described.append(
                value
                if description is None
                else value.model_copy(update={"description": description})
            )
            continue
        description = _description(_function_tool(value).name, values, mcp=mcp)
        described.append(
            _function_tool(value)
            if description is None
            else _described_function_tool(value, description)
        )
    return described


def warm_tool_schemas(
    tools: Sequence[BaseTool | Callable[..., object]],
    values: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """Build the described tools and the schemas models receive before any run needs them."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    for described in apply_tool_descriptions(tools, values):
        convert_to_openai_tool(described)


def _deployment_context() -> tuple[str, str]:
    """Identify the agent service deployment from explicit configuration."""
    if configured := ENV.OPENSWE_ENV.optional():
        environment = configured.lower()
        return "production" if environment == "prod" else environment, "OPENSWE_ENV"
    if ENV.LANGSMITH_LANGGRAPH_API_VARIANT.optional() == "local_dev":
        return "local", "LANGSMITH_LANGGRAPH_API_VARIANT"
    return "unknown", "no explicit environment or local runtime marker"


def common_prompt(agent_prompt: str = "") -> str:
    """Add deployment context only for preview and staging services."""
    environment, source = _deployment_context()
    if environment not in {"preview", "staging"}:
        return agent_prompt
    shared = prompt("system/common", deployment_environment=environment, deployment_source=source)
    return f"{shared}\n\n{agent_prompt}" if agent_prompt else shared
