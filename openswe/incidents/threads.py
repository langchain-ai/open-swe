"""Each incident is its Slack channel's agent thread.

The thread's metadata holds the incident under ``incident`` and its id under
``incident_id``, which is what finds it. The incident's activity lives in its
history document, which outlives the thread's retention.
"""

import logging
from collections.abc import Collection, Mapping

from pydantic import ValidationError

from openswe.incidents.document_models import IncidentHistory
from openswe.incidents.models import Activity, Incident, IncidentId
from openswe.incidents.records import HISTORY
from openswe.store import now_iso
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import ThreadLike, thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks.common import is_not_found_error

logger = logging.getLogger(__name__)

INCIDENT_SOURCE = "incidents_agent"
_INCIDENT_KEY = "incident"
_PAGE = 100


def _thread_id(thread: ThreadLike) -> str:
    value = thread.get("thread_id") if isinstance(thread, Mapping) else None
    return value if isinstance(value, str) else ""


def incident_of(thread: ThreadLike) -> Incident | None:
    """The incident a thread's metadata holds, without its activity; ``None`` if it holds none."""
    metadata = thread_metadata(thread)
    value = metadata.get(_INCIDENT_KEY)
    if metadata.get("source") != INCIDENT_SOURCE or not isinstance(value, dict):
        return None
    try:
        record = Incident.model_validate(value)
    except ValidationError:
        logger.warning(
            "Skipping unreadable incident thread",
            extra={"agent_thread_id": _thread_id(thread)},
            exc_info=True,
        )
        return None
    record.thread_id = _thread_id(thread)
    return record


class IncidentThreads:
    """Every incident, read from and written to its agent thread."""

    async def get(self, id: IncidentId) -> Incident | None:
        threads = await langgraph_client().threads.search(
            metadata={"source": INCIDENT_SOURCE, "incident_id": id}, limit=1
        )
        record = next((incident_of(thread) for thread in threads), None)
        return None if record is None else await self._with_activity(record)

    async def thread(self, thread_id: str) -> ThreadLike | None:
        """The agent thread, or ``None`` once its retention ended."""
        try:
            return await langgraph_client().threads.get(thread_id)
        except Exception as exc:
            if is_not_found_error(exc):
                return None
            raise

    async def for_thread(self, thread_id: str) -> Incident | None:
        thread = await self.thread(thread_id)
        record = None if thread is None else incident_of(thread)
        return None if record is None else await self._with_activity(record)

    async def in_workspace(
        self, workspace_id: str, statuses: Collection[str] | None = None
    ) -> list[Incident]:
        """The Slack workspace's incidents, only those in ``statuses`` when given."""
        records: list[Incident] = []
        offset = 0
        while page := await langgraph_client().threads.search(
            metadata={"source": INCIDENT_SOURCE}, limit=_PAGE, offset=offset
        ):
            records.extend(
                record
                for thread in page
                if (record := incident_of(thread)) is not None
                and record.workspace_id == workspace_id
                and (statuses is None or record.status in statuses)
            )
            if len(page) < _PAGE:
                break
            offset += len(page)
        activity = {thread_id: history.activity for thread_id, history in await HISTORY.all()}
        for record in records:
            record.activity = activity.get(record.thread_id, [])
        return records

    async def history(
        self, id: IncidentId, workspace_id: str
    ) -> tuple[str, IncidentHistory] | None:
        """The thread id and history document of the Slack workspace's incident ``id``.

        Found even after the thread's retention ended.
        """
        return next(
            (
                (thread_id, history)
                for thread_id, history in await HISTORY.where("id", id)
                if history.workspace_id == workspace_id
            ),
            None,
        )

    async def any(self) -> bool:
        threads = await langgraph_client().threads.search(
            metadata={"source": INCIDENT_SOURCE}, limit=1
        )
        return bool(threads)

    async def save(self, record: Incident, *, by: str | None = None) -> None:
        """Write the incident to its thread and its history document.

        ``by`` is the GitHub login of the person whose action changed it.
        """
        record.updated_at = now_iso()
        await self.write_metadata(record)
        await self.record_history(record, activity=record.activity, by=by)

    async def write_metadata(self, record: Incident) -> None:
        """Write the incident to its thread's metadata as it is, ``updated_at`` included."""
        await langgraph_client().threads.update(
            thread_id=record.thread_id,
            metadata={"incident_id": record.id, _INCIDENT_KEY: record.model_dump(mode="json")},
        )

    async def record_history(
        self,
        record: Incident,
        *,
        activity: list[Activity] | None = None,
        by: str | None = None,
    ) -> None:
        """Refresh the incident's history document; ``activity`` replaces the stored activity."""
        previous = await HISTORY.get(record.thread_id)
        await HISTORY.put(
            record.thread_id,
            IncidentHistory(
                id=record.id,
                workspace_id=record.workspace_id,
                channel_id=record.channel_id,
                channel_name=record.channel_name or (previous.channel_name if previous else ""),
                title=record.title or (previous.title if previous else record.channel_name),
                status=record.status,
                created_at=record.created_at,
                updated_at=now_iso(),
                activity=activity
                if activity is not None
                else previous.activity
                if previous
                else [],
            ),
            by=by,
        )
        await Topic.INCIDENTS.invalidate(key=record.id)

    async def _with_activity(self, record: Incident) -> Incident:
        history = await HISTORY.get(record.thread_id)
        record.activity = history.activity if history else []
        return record
