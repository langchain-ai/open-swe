"""Deployment-scoped opaque analytics identities."""

from uuid import UUID

from agent.analytics.events import person_uuid, subject_uuid
from agent.database.analytics import configured, workspace_id


def opaque_id(kind: str, value: str | int | None) -> UUID | None:
    if not configured():
        return None
    normalized = str(value or "").strip()
    return subject_uuid(workspace_id(), kind, normalized) if normalized else None


def opaque_person(provider: str, immutable_id: str | int | None) -> UUID | None:
    if not configured():
        return None
    normalized = str(immutable_id or "").strip().lower()
    return person_uuid(workspace_id(), provider, normalized) if normalized else None
