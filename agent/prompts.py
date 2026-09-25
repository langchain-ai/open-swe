from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from pathlib import PurePosixPath
from string import Template
from typing import Any

from jinja2 import Environment, FunctionLoader, StrictUndefined
from langchain_core.tools import BaseTool

_PROMPT_ROOT = resources.files("agent.resources").joinpath("prompts")


def _prompt_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.name.endswith((".md", ".md.jinja")) or ".." in path.parts:
        raise ValueError(f"invalid prompt resource path: {name!r}")
    return path


@cache
def load_prompt(name: str) -> str:
    path = _prompt_path(name)
    resource = _PROMPT_ROOT.joinpath(*path.parts)
    return resource.read_text(encoding="utf-8").strip()


def render_prompt(name: str, values: Mapping[str, object] | None = None, **kwargs: object) -> str:
    substitutions = {**(values or {}), **kwargs}
    return Template(load_prompt(name)).substitute(substitutions)


_JINJA = Environment(
    loader=FunctionLoader(load_prompt),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(name: str, **values: object) -> str:
    """Render a ``.md.jinja`` prompt; ``{% include %}`` resolves against the prompt root."""
    if not name.endswith(".md.jinja"):
        raise ValueError(f"Jinja prompts must end in .md.jinja: {name!r}")
    return _JINJA.get_template(name).render(values).strip()


def apply_tool_descriptions(
    tools: Sequence[Any],
    values: Mapping[str, Mapping[str, object]] | None = None,
) -> list[Any]:
    """Describe each tool from ``tools/<name>.md``, rendering the ones with ``values``."""
    described: list[Any] = []
    for value in tools:
        name = getattr(value, "name", None) or getattr(value, "__name__", None)
        if not isinstance(name, str) or not name:
            described.append(value)
            continue
        substitutions = (values or {}).get(name)
        try:
            description = (
                render_prompt(f"tools/{name}.md", substitutions)
                if substitutions is not None
                else load_prompt(f"tools/{name}.md")
            )
        except FileNotFoundError:
            described.append(value)
            continue
        if isinstance(value, BaseTool):
            described.append(value.model_copy(update={"description": description}))
        else:
            value.__doc__ = description
            described.append(value)
    return described
