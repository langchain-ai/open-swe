from datetime import timedelta

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.incidents import documents, service
from openswe.incidents.document_models import IncidentHistory
from openswe.incidents.import_records import (
    COMMANDS_NAMESPACE,
    HISTORY_NAMESPACE,
    INCIDENTS_NAMESPACE,
    POLICIES_NAMESPACE,
    REPORTS_NAMESPACE,
    SUMMARIES_NAMESPACE,
    import_store_incidents,
)
from openswe.incidents.models import (
    CommandReceipt,
    Incident,
    IncidentId,
    IncidentPolicy,
    IncidentReport,
    IncidentReportRecord,
)
from openswe.store import now_iso
from tests.conftest import FakeStore

SAVED = "2026-01-01T00:00:00+00:00"
INCIDENT_ID = IncidentId("i1")


@pytest.fixture
def store(registry_db: None, fake_store: FakeStore) -> FakeStore:
    return fake_store


def _receipt(id: str, incident_id: str = INCIDENT_ID) -> dict[str, object]:
    return CommandReceipt(
        id=id, incident_id=IncidentId(incident_id), action="ask", content_hash="h"
    ).model_dump(mode="json")


async def test_store_import_copies_incident_state_then_retires_the_store_records(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = Incident(
        id=INCIDENT_ID, workspace_id="T1", channel_id="C1", thread_id="t1", updated_at=SAVED
    )
    policy = IncidentPolicy(enabled=True, workspace_id="T1", version=2)
    store.seed(POLICIES_NAMESPACE, "default", policy.model_dump(mode="json"))
    store.seed(INCIDENTS_NAMESPACE, incident.id, incident.model_dump(mode="json"))
    report = IncidentReportRecord(
        incident_id=INCIDENT_ID,
        report=IncidentReport(summary="Errors"),
        digest="d",
        updated_at=SAVED,
    )
    store.seed(REPORTS_NAMESPACE, incident.id, report.model_dump(mode="json"))
    history = IncidentHistory(
        id=INCIDENT_ID,
        workspace_id="T1",
        channel_id="C1",
        title="API",
        created_at=SAVED,
        updated_at=SAVED,
    )
    store.seed(HISTORY_NAMESPACE, incident.id, history.model_dump(mode="json"))
    store.seed(SUMMARIES_NAMESPACE, incident.id, {"markdown": "# API"})
    store.seed(COMMANDS_NAMESPACE, "c1", _receipt("c1"))
    # A report lives on its incident's row, so one without an incident waits in the Store.
    orphan = report.model_copy(update={"incident_id": "gone"})
    store.seed(REPORTS_NAMESPACE, "gone", orphan.model_dump(mode="json"))

    assert await import_store_incidents() == StoreImport(moved=6)
    assert await service.get_policy() == policy
    assert await service.INCIDENTS.for_thread("t1") == incident
    assert await service.REPORTS.get(INCIDENT_ID) == report
    assert await documents.HISTORY.get(INCIDENT_ID) == history
    assert await documents.POSTMORTEMS.get(INCIDENT_ID) == "# API"
    assert await service.COMMANDS.get("c1") is not None
    assert await import_store_incidents() == StoreImport(waiting=6)

    paused = incident.model_copy(update={"status": "paused", "updated_at": now_iso()})
    store.seed(INCIDENTS_NAMESPACE, incident.id, paused.model_dump(mode="json"))
    assert await import_store_incidents() == StoreImport(moved=1, waiting=5)
    assert await service.INCIDENTS.get(INCIDENT_ID) == paused

    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_incidents() == StoreImport(moved=6)
    assert {namespace for namespace, items in store.items.items() if items} == {REPORTS_NAMESPACE}
    assert await service.COMMANDS.get("c1") is not None


async def test_store_import_drops_receipts_an_old_replica_deleted_from_the_store(
    store: FakeStore,
) -> None:
    await service.INCIDENTS.put(
        INCIDENT_ID, Incident(id=INCIDENT_ID, workspace_id="T1", channel_id="C1", thread_id="t1")
    )
    store.seed(COMMANDS_NAMESPACE, "failed", _receipt("failed"))
    store.seed(COMMANDS_NAMESPACE, "kept", _receipt("kept"))
    # A receipt links to its incident's row, so one without an incident waits in the Store.
    store.seed(COMMANDS_NAMESPACE, "orphan", _receipt("orphan", "gone"))
    assert await import_store_incidents() == StoreImport(moved=2)
    assert await service.COMMANDS.claim(CommandReceipt.model_validate(_receipt("fresh")))

    store.items[COMMANDS_NAMESPACE].pop("failed")

    assert await import_store_incidents() == StoreImport(moved=1, waiting=1)
    assert await service.COMMANDS.get("failed") is None
    assert await service.COMMANDS.get("kept") is not None
    assert await service.COMMANDS.get("fresh") is not None
    assert await service.COMMANDS.get("orphan") is None
    assert "orphan" in store.items[COMMANDS_NAMESPACE]
