"""Repo-bundled analyzer skills, served to the agent as virtual files.

The two analyzer playbooks live as ``SKILL.md`` files under ``agent/skills/``.
They are surfaced to the deepagents ``SkillsMiddleware`` via a ``StateBackend``
mounted at ``/skills/`` in a ``CompositeBackend`` — so the agent reads them with
``read_file`` without anything ever being written to the execution sandbox.

The ``files`` channel is seeded at invoke time (see the launchers). Because
``CompositeBackend`` strips the ``/skills/`` route prefix before delegating to the
``StateBackend``, the seeded keys are the *stripped* paths (e.g.
``/bootstrap-repo-analysis/SKILL.md``), while the agent and ``SkillsMiddleware``
address them under ``/skills/...``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILLS_ROUTE = "/skills/"

BOOTSTRAP_SKILL = "bootstrap-repo-analysis"
CONTINUAL_SKILL = "continual-learning"

ANALYZER_MODES = {"bootstrap": BOOTSTRAP_SKILL, "continual": CONTINUAL_SKILL}


def skill_path_for_mode(mode: str) -> str:
    """Return the agent-facing ``/skills/<name>/SKILL.md`` path for a run mode."""
    skill = ANALYZER_MODES.get(mode, BOOTSTRAP_SKILL)
    return f"{SKILLS_ROUTE}{skill}/SKILL.md"


def build_skill_files() -> dict[str, Any]:
    """Return ``{stripped_path: FileData}`` for every bundled analyzer skill.

    Seed this into the run input's ``files`` so the ``/skills/`` StateBackend route
    can serve them. Keys omit the ``/skills`` prefix (stripped by the composite
    route); values are ``FileData`` v2 entries.
    """
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from deepagents.backends.utils import create_file_data

    files: dict[str, Any] = {}
    for skill in ANALYZER_MODES.values():
        skill_md = SKILLS_DIR / skill / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        files[f"/{skill}/SKILL.md"] = create_file_data(text)
    return files
