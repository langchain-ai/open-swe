"""``mda/bundled_skills`` ships the skills ``openswe/bundled_skills`` holds, unchanged.

The MDA runtime serves its copy itself (``mda/rt/skills.py``); the backend lists its
own only for an MDA deployment from before that, so the two must not drift until the
backend's goes.
"""

from pathlib import Path

from openswe.skill_store.run_skills import BUNDLED_SKILLS_DIR

_MDA_COPY = Path(__file__).parents[2] / "mda" / "bundled_skills"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_the_mda_runtime_ships_the_backends_bundled_skills() -> None:
    assert _files(_MDA_COPY) == _files(BUNDLED_SKILLS_DIR)
    assert _files(_MDA_COPY)
