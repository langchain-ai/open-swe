"""Curated incident history and postmortem, independent from operational retention."""

from pydantic import BaseModel, Field

from openswe.incidents.models import Activity, IncidentId


class IncidentHistory(BaseModel):
    """What stays known about an incident after its thread's retention ends."""

    id: IncidentId
    workspace_id: str
    channel_id: str
    channel_name: str = ""
    title: str = ""
    status: str = ""
    created_at: str
    updated_at: str
    # Enrollment, controls, and failures: what happened outside the thread's runs.
    activity: list[Activity] = Field(default_factory=list)


class IncidentPostmortem(BaseModel):
    """The latest agent postmortem, as Markdown."""

    markdown: str
