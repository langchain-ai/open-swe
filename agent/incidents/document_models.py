"""Curated incident history, independent from operational retention."""

from pydantic import BaseModel


class IncidentHistory(BaseModel):
    id: str
    workspace_id: str
    channel_id: str
    channel_name: str = ""
    title: str = ""
    status: str = ""
    created_at: str
    updated_at: str
