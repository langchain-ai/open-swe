"""The model-facing prompts in ``prompts/``, rendered the same on either side."""

from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import PurePosixPath

from jinja2 import Environment, FunctionLoader, StrictUndefined

_PROMPT_ROOT = resources.files("openswe_shared").joinpath("prompts")


def _prompt_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.name.endswith((".md", ".md.jinja")) or ".." in path.parts:
        raise ValueError(f"invalid prompt resource path: {name!r}")
    return path


@cache
def load_prompt(name: str) -> str:
    """The prompt file ``name`` (with its extension), as written."""
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
def is_template(name: str) -> bool:
    """Whether the prompt ``name`` is a Jinja template, ``<name>.md.jinja``."""
    path = _prompt_path(f"{name}.md.jinja")
    return _PROMPT_ROOT.joinpath(*path.parts).is_file()


def prompt(name: str, values: Mapping[str, object] | None = None, /, **kwargs: object) -> str:
    """Render Jinja templates or load static Markdown prompts without substitutions."""
    substitutions = {**(values or {}), **kwargs}
    if is_template(name):
        return _JINJA.get_template(f"{name}.md.jinja").render(substitutions).strip()
    if substitutions:
        raise ValueError(f"prompt {name!r} does not accept variables without a Jinja template")
    return load_prompt(f"{name}.md")
