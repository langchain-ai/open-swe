"""``mda/prompts/model-selection`` holds the backend's route classifier prompts, unchanged.

The MDA run classifies a thread's route itself (``mda/rt/routing.py``), and the backend
still does for the runs it answers from their plan: the two must route alike.
"""

from pathlib import Path

import pytest
from runtime_api.route_questions import TaskRoute

from openswe.prompts import prompt

_MDA_PROMPTS = Path(__file__).parents[2] / "mda" / "prompts" / "model-selection"
_NAMES = [
    *(f"task-type/{name}" for name in ("instructions", *TaskRoute.TASK_TYPES)),
    *(f"complexity/{name}" for name in ("instructions", *TaskRoute.COMPLEXITIES)),
]


@pytest.mark.parametrize("name", _NAMES)
def test_the_mda_route_prompts_match_the_backends(name: str) -> None:
    copy = (_MDA_PROMPTS / f"{name}.md.jinja").read_text(encoding="utf-8").strip()

    assert copy == prompt(f"model-selection/{name}")


def test_the_mda_route_prompts_are_the_questions() -> None:
    copies = {
        str(path.relative_to(_MDA_PROMPTS)).removesuffix(".md.jinja")
        for path in _MDA_PROMPTS.rglob("*.md.jinja")
    }
    assert copies == set(_NAMES)
