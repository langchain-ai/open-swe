from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from pathlib import PurePosixPath
from string import Template
from typing import Any

from langchain_core.tools import BaseTool

_PROMPT_ROOT = resources.files("agent.resources").joinpath("prompts")


def _prompt_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or path.suffix != ".md" or ".." in path.parts:
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


def apply_tool_descriptions(tools: Sequence[Any]) -> list[Any]:
    described: list[Any] = []
    for value in tools:
        name = getattr(value, "name", None) or getattr(value, "__name__", None)
        if not isinstance(name, str) or not name:
            described.append(value)
            continue
        try:
            description = load_prompt(f"tools/{name}.md")
        except FileNotFoundError:
            described.append(value)
            continue
        if isinstance(value, BaseTool):
            described.append(value.model_copy(update={"description": description}))
        else:
            value.__doc__ = description
            described.append(value)
    return described
