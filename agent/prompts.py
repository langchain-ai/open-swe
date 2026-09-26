from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from pathlib import PurePosixPath
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


_JINJA = Environment(
    loader=FunctionLoader(load_prompt),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@cache
def _is_template(name: str) -> bool:
    path = _prompt_path(f"{name}.md.jinja")
    return _PROMPT_ROOT.joinpath(*path.parts).is_file()


def prompt(name: str, values: Mapping[str, object] | None = None, /, **kwargs: object) -> str:
    """Render Jinja templates or load static Markdown prompts without substitutions."""
    substitutions = {**(values or {}), **kwargs}
    if _is_template(name):
        return _JINJA.get_template(f"{name}.md.jinja").render(substitutions).strip()
    if substitutions:
        raise ValueError(f"prompt {name!r} does not accept variables without a Jinja template")
    return load_prompt(f"{name}.md")


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
            description = prompt(f"tools/{name}", substitutions)
        except FileNotFoundError:
            described.append(value)
            continue
        if isinstance(value, BaseTool):
            described.append(value.model_copy(update={"description": description}))
        else:
            value.__doc__ = description
            described.append(value)
    return described
