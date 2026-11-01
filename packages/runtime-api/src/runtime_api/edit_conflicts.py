"""Best-effort file overlap checks after explicit edits."""

from pydantic import BaseModel, Field

from runtime_api.endpoint import Endpoint
from runtime_api.operations import RunRequest


class EditedFile(RunRequest):
    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$", max_length=255)
    path: str = Field(min_length=1, max_length=4096)
    branch: str = Field(default="", max_length=1024)


class EditConflicts(BaseModel):
    reminder: str = ""


CHECK_EDIT_CONFLICTS = Endpoint(
    "check_edit_conflicts", EditedFile, EditConflicts, seconds=5, delivery="idempotent"
)
