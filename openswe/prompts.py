import re
from collections.abc import Callable, Mapping, Sequence
from functools import cache, lru_cache
from importlib import resources
from pathlib import PurePosixPath

from jinja2 import Environment, FunctionLoader, StrictUndefined
from langchain_core.tools import BaseTool, tool

from openswe.config import ENV

_PROMPT_ROOT = resources.files("openswe.resources").joinpath("prompts")


def _prompt_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.name.endswith((".md", ".md.jinja")) or ".." in path.parts:
        raise ValueError(f"invalid prompt resource path: {name!r}")
    return path


@cache
def _load_prompt(name: str) -> str:
    path = _prompt_path(name)
    resource = _PROMPT_ROOT.joinpath(*path.parts)
    return resource.read_text(encoding="utf-8").strip()


_JINJA = Environment(
    loader=FunctionLoader(_load_prompt),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@cache
def _is_template(name: str) -> bool:
    path = _prompt_path(f"{name}.md.jinja")
    return _PROMPT_ROOT.joinpath(*path.parts).is_file()


@cache
def _closing_tag_re(tags: tuple[str, ...]) -> re.Pattern[str]:
    # XML tolerates whitespace around the tag name (`</body >`, `</ body\n>`).
    return re.compile(r"</\s*(" + "|".join(map(re.escape, tags)) + r")\s*>", re.IGNORECASE)


def escape_for_data_block(text: str, tags: Sequence[str]) -> str:
    """Neutralize closing ``tags`` so untrusted text can't break out of its data block.

    Each end tag becomes an inert ``</name_>`` that stays readable but no longer closes.
    """
    return _closing_tag_re(tuple(tags)).sub(lambda m: f"</{m.group(1).lower()}_>", text)


def prompt(name: str, values: Mapping[str, object] | None = None, /, **kwargs: object) -> str:
    """Render Jinja templates or load static Markdown prompts without substitutions."""
    substitutions = {**(values or {}), **kwargs}
    if _is_template(name):
        return _JINJA.get_template(f"{name}.md.jinja").render(substitutions).strip()
    if substitutions:
        raise ValueError(f"prompt {name!r} does not accept variables without a Jinja template")
    return _load_prompt(f"{name}.md")


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
        if _is_template(template):
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
