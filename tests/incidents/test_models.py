import pytest
from pydantic import ValidationError

from agent.incidents.models import Incident, IncidentPolicy, IncidentReport, IncidentReportRecord


@pytest.mark.parametrize("prefix", ["", "#", "inc *", "*"])
def test_rejects_prefixes_that_could_enroll_unrelated_channels(prefix):
    with pytest.raises(ValidationError):
        IncidentPolicy(channel_prefix=prefix)


def test_normalizes_prefix():
    assert IncidentPolicy(channel_prefix="#Inc-").channel_prefix == "inc-"


def test_model_call_budget_is_bounded():
    with pytest.raises(ValidationError):
        IncidentPolicy(max_model_calls=0)
    with pytest.raises(ValidationError):
        IncidentPolicy(max_model_calls=21)


def test_incident_starts_watching_before_its_thread_exists():
    record = Incident(id="i1", workspace_id="T1", channel_id="C1")
    assert record.status == "watching"
    assert record.thread_id == ""
    assert record.activity == []


def test_report_record_round_trips_through_the_store_shape():
    record = IncidentReportRecord(
        incident_id="i1", report=IncidentReport(summary="Errors [slack:1]"), digest="d1"
    )
    assert IncidentReportRecord.model_validate(record.model_dump()) == record
