"""Curated incident history, independent from operational retention."""

from typing import Literal

from pydantic import BaseModel, Field

from agent.store import now_iso

DocumentKind = Literal["postmortem", "status_page_draft"]


class DocumentReference(BaseModel):
    id: str
    source: str
    url: str = ""


class DocumentRevision(BaseModel):
    incident_id: str
    kind: DocumentKind
    revision: int
    expected_revision: int
    markdown: str
    operation_id: str
    author: str
    source: Literal["agent", "responder"]
    run_id: str = ""
    created_at: str = Field(default_factory=now_iso)
    evidence: list[DocumentReference] = Field(default_factory=list)


class IncidentHistory(BaseModel):
    id: str
    workspace_id: str
    channel_id: str
    channel_name: str = ""
    title: str = ""
    status: str = ""
    created_at: str
    updated_at: str
