"""The model-facing prompts only this deployment renders, in ``prompts/``.

The backend's prompts live in ``openswe/resources/prompts``; a prompt moves here
when only a channel handler here writes it (AGENTS.md's exception for channel
input). Rendered as the backend renders its own: the file stripped, Jinja with
every variable required, the result stripped.
"""

from functools import cache
from pathlib import Path
from typing import Final

from jinja2 import Environment, FunctionLoader, StrictUndefined

_PROMPTS: Final = Path(__file__).resolve().parent.parent / "prompts"


@cache
def _load(name: str) -> str:
    path = (_PROMPTS / name).resolve()
    if not path.is_relative_to(_PROMPTS):
        raise ValueError(f"invalid prompt path: {name!r}")
    return path.read_text(encoding="utf-8").strip()


_JINJA: Final = Environment(
    loader=FunctionLoader(_load),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def prompt(name: str, /, **values: object) -> str:
    """The template ``prompts/<name>.md.jinja``, rendered with ``values``."""
    return _JINJA.get_template(f"{name}.md.jinja").render(values).strip()
