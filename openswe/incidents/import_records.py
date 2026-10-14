"""Move incident state from the LangGraph Store into PostgreSQL.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

A Store record is copied, and deleted only once the copy has been in place for
a day: replicas of the previous release read and write these records until
their rollout finishes. A Store record saved after the copy, by such a replica,
replaces it: by its ``updated_at``, the policy by its ``version``, and a
postmortem by its incident history's ``updated_at``, which is saved with it.
Only command receipts are ever deleted, so a copied receipt whose Store record
such a replica deleted goes too.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from openswe.database.store_imports import (
    RETIRE_STORE_COPY_AFTER,
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
)
from openswe.incidents.document_models import IncidentHistory
from openswe.incidents.documents import HISTORY, POSTMORTEMS
from openswe.incidents.models import (
    CommandReceipt,
    Incident,
    IncidentId,
    IncidentPolicy,
    IncidentReportRecord,
)
from openswe.incidents.service import COMMANDS, INCIDENTS, POLICIES, REPORTS
from openswe.store import Namespace, StoreEntry, delete_value, search_all_entries

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
    return (
        await _import_policy()
        + await _import_records(INCIDENTS_NAMESPACE, Incident, INCIDENTS.written_at, INCIDENTS.put)
        + await _import_records(
            REPORTS_NAMESPACE, IncidentReportRecord, REPORTS.written_at, REPORTS.put
        )
        + await _import_records(HISTORY_NAMESPACE, IncidentHistory, HISTORY.written_at, HISTORY.put)
        + await _import_postmortems(histories)
        + await _import_commands()
    )


async def _entries(namespace: Namespace) -> list[StoreEntry]:
    return [
        entry
        for entry in await search_all_entries(namespace)
        if tuple(entry.namespace or namespace) == tuple(namespace)
    ]


async def _retire(namespace: Namespace, key: str, written_at: datetime | None) -> StoreImport:
    """Delete a Store record once its PostgreSQL copy has settled."""
    if not may_delete_store_copy(written_at):
        return StoreImport(waiting=1)
    await delete_value(namespace, key)
    return StoreImport(moved=1)


def _parse[RecordT: BaseModel](
    model: type[RecordT], namespace: Namespace, entry: StoreEntry
) -> RecordT | None:
    try:
        return model.model_validate(entry.value)
    except ValidationError:
        logger.warning(
            "Leaving an unreadable incident record in the Store",
            extra={"store_namespace": "/".join(namespace), "store_key": entry.key},
            exc_info=True,
        )
        return None


async def _import_policy() -> StoreImport:
    result = StoreImport()
    for entry in await _entries(POLICIES_NAMESPACE):
        if entry.key != _POLICY_KEY:
            continue
        stored = _parse(IncidentPolicy, POLICIES_NAMESPACE, entry)
        if stored is None:
            continue
        current = await POLICIES.get()
        if current is None or stored.version > current.version:
            await POLICIES.put(stored)
            result += StoreImport(moved=1)
        else:
            result += await _retire(POLICIES_NAMESPACE, entry.key, await POLICIES.written_at())
    return result


async def _import_records[RecordT: BaseModel](
    namespace: Namespace,
    model: type[RecordT],
    written_at: Callable[[IncidentId], Awaitable[datetime | None]],
    put: Callable[[IncidentId, RecordT], Awaitable[RecordT]],
) -> StoreImport:
    result = StoreImport()
    for entry in await _entries(namespace):
        record = _parse(model, namespace, entry)
        if record is None:
            continue
        key = IncidentId(entry.key)
        current = await written_at(key)
        if current is None or store_copy_is_newer(entry.value, current):
            await put(key, record)
            result += StoreImport(moved=1)
        else:
            result += await _retire(namespace, entry.key, current)
    return result


async def _import_postmortems(histories: Mapping[str, Mapping[str, object]]) -> StoreImport:
    result = StoreImport()
    for entry in await _entries(SUMMARIES_NAMESPACE):
        markdown = entry.value.get("markdown")
        if not isinstance(markdown, str):
            logger.warning(
                "Leaving an unreadable incident postmortem in the Store",
                extra={"store_key": entry.key},
            )
            continue
        key = IncidentId(entry.key)
        current = await POSTMORTEMS.written_at(key)
        if current is None or store_copy_is_newer(histories.get(entry.key, {}), current):
            await POSTMORTEMS.put(key, markdown)
            result += StoreImport(moved=1)
        else:
            result += await _retire(SUMMARIES_NAMESPACE, entry.key, current)
    return result


async def _import_commands() -> StoreImport:
    result = StoreImport()
    present: set[str] = set()
    for entry in await _entries(COMMANDS_NAMESPACE):
        present.add(entry.key)
        receipt = _parse(CommandReceipt, COMMANDS_NAMESPACE, entry)
        if receipt is None:
            continue
        copied_at = await COMMANDS.written_at(entry.key)
        if copied_at is None:
            await COMMANDS.claim(receipt, store_copy=True)
            result += StoreImport(moved=1)
        else:
            result += await _retire(COMMANDS_NAMESPACE, entry.key, copied_at)
    # Until the import retires a copy's Store record, a missing one was deleted there.
    deleted = await COMMANDS.delete_store_copies(
        present, datetime.now(UTC) - RETIRE_STORE_COPY_AFTER
    )
    if deleted:
        logger.info(
            "Deleted incident command receipts deleted in the Store", extra={"deleted": deleted}
        )
    return result + StoreImport(moved=deleted)
