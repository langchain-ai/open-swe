"""The skills a run may load, as the backend keeps them for it.

A skill is a folder under one of the run's skill sources (``Catalog.skill_sources``)
holding its ``SKILL.md``. The backend lists only the skills the run may see: a
person's own only on a run that acts for them.
"""

from pydantic import BaseModel

from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest


class SkillFile(BaseModel):
    # Where the run reads it, such as ``/skills/deploy/SKILL.md``.
    path: str
    content: str


class RunSkills(BaseModel):
    files: list[SkillFile] = []


# Only reads, so it is safe to send again.
SKILLS = Endpoint("skills", RunRequest, RunSkills, seconds=LOOP_CALL_SECONDS, delivery="idempotent")
