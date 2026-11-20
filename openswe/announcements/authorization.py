"""Authorization for a human decision on an agent-prepared broadcast."""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.slack.dm import is_dm_channel
from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.identity import ThreadIdentity
from openswe.threads.store import THREADS


class ApprovalAuthorization:
    @staticmethod
    async def permits(
        thread_id: str, person_id: UUID, *, conn: AsyncConnection | None = None
    ) -> bool:
        thread = (
            await index.get(thread_id, conn=conn, for_update=True)
            if conn is not None
            else await THREADS.get(thread_id)
        )
        return thread is not None and await ApprovalAuthorization.permits_metadata(
            thread.metadata, person_id
        )

    @staticmethod
    async def permits_metadata(metadata: dict[str, JsonValue], person_id: UUID) -> bool:
        if metadata.get("visibility") != "private" or metadata.get("owner_type") != "user":
            return False
        saved_id = metadata.get("owner_user_id")
        if saved_id:
            if saved_id != str(person_id):
                return False
        else:
            owner = metadata.get("owner_login")
            if not isinstance(owner, str) or not owner:
                return False
            user = await ThreadIdentity(owner).user()
            if user is None or user.id != person_id:
                return False
        source = metadata.get("source")
        if source == "dashboard":
            return metadata.get("admin_thread") is True
        if source == "slack":
            slack = SourceContext.from_metadata(metadata).slack_thread
            return (
                slack is not None
                and slack.location is not None
                and is_dm_channel(slack.channel_context)
            )
        return False
