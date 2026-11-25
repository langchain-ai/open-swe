"""The run's skill repositories, named afresh by the backend from the authenticated run token."""

from typing import Annotated

from pydantic import BaseModel, Field

from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest

HubRepo = Annotated[str, Field(pattern=r"^-/[a-z][a-z0-9-]{0,127}$")]


class SkillsRequest(RunRequest):
    pass


class RunSkills(BaseModel):
    # Mounted at /organization-skills/; None mounts nothing there.
    organization_repo: HubRepo | None = None
    # Mounted at /my-skills/, only for the owner's own private threads.
    personal_repo: HubRepo | None = None


SKILLS = Endpoint(
    "skills", SkillsRequest, RunSkills, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
