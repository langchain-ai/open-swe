"""``mda/prompts/model-selection`` holds the backend's route classifier prompts, unchanged.

The MDA run classifies a thread's route itself (``mda/rt/routing.py``), and the backend
still does for the runs it answers from their plan: the two must route alike.
"""

from pathlib import Path

import pytest

from openswe.prompts import prompt

_MDA_PROMPTS = Path(__file__).parents[2] / "mda" / "prompts" / "model-selection"


@pytest.mark.parametrize("name", ["instructions", "fast", "balanced", "performance"])
def test_the_mda_route_prompts_match_the_backends(name: str) -> None:
    copy = (_MDA_PROMPTS / f"{name}.md.jinja").read_text(encoding="utf-8").strip()

    assert copy == prompt(f"model-selection/{name}")


def test_the_mda_routes_are_the_backends() -> None:
    from openswe.middleware.model_selection import ROUTES

    copies = {path.name.removesuffix(".md.jinja") for path in _MDA_PROMPTS.iterdir()}
    assert copies == {"instructions", *ROUTES}
