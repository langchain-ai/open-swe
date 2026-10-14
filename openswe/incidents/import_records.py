"""Move incident state from the LangGraph Store onto incident threads, their documents, and settings.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

The policy becomes the instance settings record ``incidents``. Each incident
becomes its thread's ``incident`` metadata, and its report, history (with the
incident's activity), and postmortem become the thread's documents; an
incident whose thread's retention already ended keeps only its documents, and
comes back on its channel's next event. A Store record is copied, and deleted
once its copy has gone a day without a write: replicas of the previous release
read and write these records until their rollout finishes. A Store record
saved after the copy, by such a replica, replaces it, by its own
``updated_at``; the policy by its ``version``, and a postmortem by its history
record's ``updated_at``, which is saved with it. Command receipts only dedupe
retries for a few seconds, so they are dropped, as are records with no
incident to belong to.
"""

import logging
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ValidationError

from openswe.database.store_imports import (
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
    timestamp,
)
from openswe.incidents.channels import open_thread
from openswe.incidents.document_models import IncidentHistory, IncidentPostmortem
from openswe.incidents.models import Incident, IncidentPolicy, IncidentReportRecord
from openswe.incidents.records import HISTORY, POSTMORTEMS, REPORTS
from openswe.incidents.service import INCIDENTS, POLICIES
from openswe.incidents.threads import incident_of
from openswe.store import Namespace, StoreEntry, delete_value, search_all_entries
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

POLICIES_NAMESPACE = ("incidents", "policies")
INCIDENTS_NAMESPACE = ("incidents", "incidents")
REPORTS_NAMESPACE = ("incidents", "reports")
HISTORY_NAMESPACE = ("incidents", "history")
SUMMARIES_NAMESPACE = ("incidents", "summaries")
COMMANDS_NAMESPACE = ("incidents", "commands")
_POLICY_KEY = "default"


async def import_store_incidents() -> StoreImport:
    histories = {entry.key: entry.value for entry in await _entries(HISTORY_NAMESPACE)}
    result = await _import_policy()
    threads: dict[str, str] = {}
    unreadable: set[str] = set()
    for entry in await _entries(INCIDENTS_NAMESPACE):
        record = _parse(Incident, INCIDENTS_NAMESPACE, entry)
        if record is None:
            unreadable.add(entry.key)
            continue
        result += await _import_incident(record, entry.value, histories.get(entry.key))
        threads[entry.key] = record.thread_id
    result += await _import_reports(threads, unreadable)
    result += await _import_postmortems(threads, unreadable, histories)
    for entry in await _entries(HISTORY_NAMESPACE):
        if entry.key in unreadable:
            continue
        thread_id = threads.get(entry.key)
        result += await _retire(
            HISTORY_NAMESPACE,
            entry.key,
            None if thread_id is None else await HISTORY.written_at(thread_id),
            orphan=thread_id is None,
        )
    for entry in await _entries(COMMANDS_NAMESPACE):
        await delete_value(COMMANDS_NAMESPACE, entry.key)
        result += StoreImport(moved=1)
    return result


async def _entries(namespace: Namespace) -> list[StoreEntry]:
    return [
        entry
        for entry in await search_all_entries(namespace)
        if tuple(entry.namespace or namespace) == tuple(namespace)
    ]


def _parse[ModelT: BaseModel](
    model: type[ModelT], namespace: Namespace, entry: StoreEntry
) -> ModelT | None:
    try:
        return model.model_validate(entry.value)
    except ValidationError:
        logger.warning(
            "Leaving an unreadable incident record in the Store",
            extra={"store_namespace": "/".join(namespace), "store_key": entry.key},
            exc_info=True,
        )
        return None


async def _retire(
    namespace: Namespace, key: str, written_at: datetime | None, *, orphan: bool = False
) -> StoreImport:
    """Delete a Store record once its copy has settled, or at once when nothing can use it."""
    if not orphan and not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value(namespace, key)
    return StoreImport(moved=1)


async def _import_policy() -> StoreImport:
    result = StoreImport()
    for entry in await _entries(POLICIES_NAMESPACE):
        stored = _parse(IncidentPolicy, POLICIES_NAMESPACE, entry)
        if stored is None:
            continue
        current = await POLICIES.get()
        if entry.key == _POLICY_KEY and (current is None or stored.version > current.version):
            await POLICIES.put(stored)
            result += StoreImport(moved=1)
        else:
            result += await _retire(
                POLICIES_NAMESPACE,
                entry.key,
                await POLICIES.written_at(),
                orphan=entry.key != _POLICY_KEY,
            )
    return result


async def _import_incident(
    record: Incident, value: JsonObject, history: Mapping[str, object] | None
) -> StoreImport:
    """Copy one incident onto its thread, and its activity into its history document."""
    if not record.thread_id:
        # Enrollment failed before the incident had a thread; give it the channel's.
        await open_thread(record)
    thread = await INCIDENTS.thread(record.thread_id)
    current = None if thread is None else incident_of(thread)
    stored_history = await HISTORY.get(record.thread_id)
    copied = False
    if thread is not None and (
        current is None or store_copy_is_newer(value, timestamp(current.updated_at))
    ):
        await INCIDENTS.write_metadata(record)
        copied = True
    store_history = _history(record, history)
    if stored_history is None or store_copy_is_newer(
        {"updated_at": store_history.updated_at}, timestamp(stored_history.updated_at)
    ):
        await HISTORY.put(record.thread_id, store_history)
        copied = True
    if copied:
        return StoreImport(moved=1)
    return await _retire(INCIDENTS_NAMESPACE, record.id, await HISTORY.written_at(record.thread_id))


def _history(record: Incident, value: Mapping[str, object] | None) -> IncidentHistory:
    """The Store's history record for ``record``, carrying the incident's activity."""
    fields: dict[str, object] = {
        "id": record.id,
        "workspace_id": record.workspace_id,
        "channel_id": record.channel_id,
        "channel_name": record.channel_name,
        "title": record.title or record.channel_name,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if value is not None:
        fields.update({key: item for key, item in value.items() if item not in (None, "")})
    # The incident's own saves move its history forward too.
    fields["updated_at"] = max(str(fields["updated_at"]), record.updated_at)
    fields["activity"] = [item.model_dump(mode="json") for item in record.activity]
    return IncidentHistory.model_validate(fields)


async def _import_reports(threads: Mapping[str, str], unreadable: set[str]) -> StoreImport:
    result = StoreImport()
    for entry in await _entries(REPORTS_NAMESPACE):
        if entry.key in unreadable:
            continue
        thread_id = threads.get(entry.key)
        if thread_id is None:
            result += await _retire(REPORTS_NAMESPACE, entry.key, None, orphan=True)
            continue
        stored = _parse(IncidentReportRecord, REPORTS_NAMESPACE, entry)
        if stored is None:
            continue
        current = await REPORTS.get(thread_id)
        if current is None or store_copy_is_newer(entry.value, timestamp(current.updated_at)):
            await REPORTS.put(thread_id, stored)
            result += StoreImport(moved=1)
        else:
            result += await _retire(
                REPORTS_NAMESPACE, entry.key, await REPORTS.written_at(thread_id)
            )
    return result


async def _import_postmortems(
    threads: Mapping[str, str],
    unreadable: set[str],
    histories: Mapping[str, Mapping[str, object]],
) -> StoreImport:
    result = StoreImport()
    for entry in await _entries(SUMMARIES_NAMESPACE):
        if entry.key in unreadable:
            continue
        thread_id = threads.get(entry.key)
        markdown = entry.value.get("markdown")
        if thread_id is None or not isinstance(markdown, str):
            result += await _retire(SUMMARIES_NAMESPACE, entry.key, None, orphan=True)
            continue
        current = await POSTMORTEMS.get(thread_id)
        written_at = await POSTMORTEMS.written_at(thread_id)
        if current is None or store_copy_is_newer(histories.get(entry.key, {}), written_at):
            await POSTMORTEMS.put(thread_id, IncidentPostmortem(markdown=markdown))
            result += StoreImport(moved=1)
        else:
            result += await _retire(SUMMARIES_NAMESPACE, entry.key, written_at)
    return result
