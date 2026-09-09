"""Authorized directory joins and audited named exports."""

from uuid import uuid4

from sqlalchemy import text

from agent.analytics.database import transaction
from agent.analytics.emitter import opaque_person, workspace_id


async def audit_named_export(*, actor_login: str, scope: str) -> None:
    actor = opaque_person("github", actor_login)
    if actor is None:
        raise ValueError("named exports require an identified actor")
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO named_export_audit (audit_id, workspace_id, actor_person_id, scope) "
                "VALUES (:audit_id, :workspace_id, :actor_person_id, :scope)"
            ),
            {
                "audit_id": uuid4(),
                "workspace_id": workspace_id(),
                "actor_person_id": actor,
                "scope": scope[:200],
            },
        )
