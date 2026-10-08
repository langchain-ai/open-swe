"""Attach verified target workspace metadata without changing request outcomes."""

import asyncio
import logging
from typing import TYPE_CHECKING

from starlette.requests import HTTPConnection

from agent.audit_logs.models import AuditLog
from agent.database import postgres

if TYPE_CHECKING:
    from agent.workspaces.store import Workspace

logger = logging.getLogger(__name__)


async def enrich_workspace(entry: AuditLog, workspace: Workspace | str) -> None:
    from agent.workspaces.store import WORKSPACES, slugify

    try:
        slug = slugify(workspace) if isinstance(workspace, str) else workspace.slug
        if entry.workspace_id is not None and entry.enrichments.workspace == slug:
            return
        if not postgres.configured():
            return
        async with asyncio.timeout(2):
            workspace_id = await WORKSPACES.id_for_slug(slug)
        if workspace_id is not None:
            entry.workspace_id = workspace_id
            entry.enrichments.workspace = slug
    except Exception:
        logger.warning(
            "Could not resolve audit target workspace",
            extra={"audit_log_id": str(entry.id)},
            exc_info=True,
        )


async def bind_workspace(request: HTTPConnection, workspace: Workspace | str) -> None:
    entry = getattr(request.state, "audit_log", None)
    if isinstance(entry, AuditLog):
        await enrich_workspace(entry, workspace)
