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
    Activity,
    Incident,
    IncidentId,
    IncidentPolicy,
    IncidentReport,
    IncidentReportRecord,
)
from openswe.store import now_iso
from tests.conftest import FakeStore, FakeThreads

SAVED = "2026-01-01T00:00:00+00:00"
INCIDENT_ID = IncidentId("i1")


@pytest.fixture
def store(incident_threads: FakeThreads, fake_store: FakeStore) -> FakeStore:
    incident_threads.add("t1", source="incidents_agent", owner_type="system", visibility="public")
    return fake_store


def _incident(**fields: object) -> Incident:
    return Incident.model_validate(
        {
            "id": INCIDENT_ID,
            "workspace_id": "T1",
            "channel_id": "C1",
            "channel_name": "inc-api",
            "thread_id": "t1",
            "updated_at": SAVED,
            "activity": [Activity(type="enrolled", summary="Following #inc-api.")],
            **fields,
        }
    )


def _seed(store: FakeStore, record: Incident) -> None:
    store.seed(
        INCIDENTS_NAMESPACE,
        record.id,
        {
            **record.model_dump(mode="json"),
            "thread_id": record.thread_id,
            "activity": [item.model_dump(mode="json") for item in record.activity],
        },
    )


async def test_store_import_moves_incidents_onto_their_threads_then_retires_the_store(
    store: FakeStore, incident_threads: FakeThreads, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = IncidentPolicy(enabled=True, workspace_id="T1", version=2)
    store.seed(POLICIES_NAMESPACE, "default", policy.model_dump(mode="json"))
    _seed(store, _incident())
    report = IncidentReportRecord(
        incident_id=INCIDENT_ID,
        report=IncidentReport(summary="Errors"),
        digest="d",
        updated_at=SAVED,
    )
    store.seed(REPORTS_NAMESPACE, INCIDENT_ID, report.model_dump(mode="json"))
    history = IncidentHistory(
        id=INCIDENT_ID,
        workspace_id="T1",
        channel_id="C1",
        title="API",
        created_at=SAVED,
        updated_at=SAVED,
    )
    store.seed(HISTORY_NAMESPACE, INCIDENT_ID, history.model_dump(mode="json"))
    store.seed(SUMMARIES_NAMESPACE, INCIDENT_ID, {"markdown": "# API"})
    store.seed(COMMANDS_NAMESPACE, "c1", {"id": "c1"})

    assert await import_store_incidents() == StoreImport(moved=5, waiting=1)
    assert await service.get_policy() == policy
    copied = await service.INCIDENTS.get(INCIDENT_ID)
    assert copied is not None
    assert (copied.thread_id, copied.channel_name) == ("t1", "inc-api")
    assert [item.type for item in copied.activity] == ["enrolled"]
    assert await service.REPORTS.get("t1") == report
    assert (await documents.HISTORY.get("t1")).title == "API"
    assert (await documents.POSTMORTEMS.get("t1")).markdown == "# API"
    assert await import_store_incidents() == StoreImport(waiting=5)

    _seed(store, _incident(status="paused", updated_at=now_iso()))
    assert await import_store_incidents() == StoreImport(moved=1, waiting=4)
    assert (await service.INCIDENTS.get(INCIDENT_ID)).status == "paused"

    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_incidents() == StoreImport(moved=5)
    assert not any(store.items.values())


async def test_an_incident_whose_thread_expired_keeps_its_documents(
    store: FakeStore, incident_threads: FakeThreads
) -> None:
    del incident_threads.items["t1"]
    _seed(store, _incident(title="API"))
    store.seed(SUMMARIES_NAMESPACE, INCIDENT_ID, {"markdown": "# API"})

    assert await import_store_incidents() == StoreImport(moved=2)
    assert await service.INCIDENTS.get(INCIDENT_ID) is None
    kept = await documents.HISTORY.get("t1")
    assert kept is not None and [item.type for item in kept.activity] == ["enrolled"]
    assert (await documents.POSTMORTEMS.get("t1")).markdown == "# API"
