"""Citation validation and context evidence for incident reports."""

import json

import pytest

from openswe.incidents import evidence_tools
from openswe.incidents.models import Evidence
from openswe.incidents.report import CONTEXT_MARKER, ReportDraft, context_evidence, finalize_report


def test_report_keeps_supported_claims_and_drops_invented_or_partial_citations():
    collected = evidence_tools.EvidenceCollector()
    collected.evidence = [Evidence(id="slack:1", source="slack", summary="Reported errors")]
    draft = ReportDraft.model_validate(
        {
            "summary": [
                {"text": "Responders observed errors", "evidence_ids": ["slack:1", "slack:1"]},
                {"text": "Invented cause", "evidence_ids": ["missing"]},
                {"text": "Unsupported inference", "evidence_ids": []},
            ],
            "impact": [{"text": "Entire fleet is down", "evidence_ids": ["slack:1", "missing"]}],
            "next_steps": [
                {"text": "Consider rolling back the reported change", "evidence_ids": ["slack:1"]},
                {"text": "Disable authentication", "evidence_ids": ["missing"]},
            ],
            "hypotheses": [
                {"title": "Observed regression", "evidence_ids": ["slack:1"]},
                {"title": "Fabricated diagnosis", "evidence_ids": ["missing"]},
            ],
        }
    )

    report = finalize_report(draft, collected)

    assert report.summary == "Responders observed errors [slack:1]"
    assert report.outcome == "findings"
    assert "Entire fleet" not in report.impact
    assert report.next_steps == ["Consider rolling back the reported change [slack:1]"]
    assert [hypothesis.title for hypothesis in report.hypotheses] == ["Observed regression"]
    assert report.gaps
    assert [evidence.id for evidence in report.evidence] == ["slack:1"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"summary": [{"text": " ", "evidence_ids": ["slack:1"]}]},
        {"summary": [{"text": "Invented cause", "evidence_ids": ["missing"]}]},
        {"gaps": [" "], "questions": [" "]},
    ],
)
def test_empty_or_fully_stripped_reports_require_repair(payload):
    collected = evidence_tools.EvidenceCollector(
        evidence=[Evidence(id="slack:1", source="slack", summary="Reported errors")]
    )
    with pytest.raises(ValueError, match="Nothing was recorded or posted"):
        finalize_report(ReportDraft.model_validate(payload), collected)


@pytest.mark.parametrize("unsupported_claim", [False, True])
def test_concrete_gaps_are_recorded_as_an_inconclusive_report(unsupported_claim):
    report = finalize_report(
        ReportDraft.model_validate(
            {
                "gaps": ["Metrics could not be checked because access was denied."],
                "summary": [{"text": "Possible outage", "evidence_ids": ["unknown"]}]
                if unsupported_claim
                else [],
            }
        ),
        evidence_tools.EvidenceCollector(),
    )
    assert report.outcome == "inconclusive"
    assert report.gaps[0] == "Metrics could not be checked because access was denied."
    assert "Possible outage" not in report.summary
    assert any("omitted" in gap for gap in report.gaps) is unsupported_claim


@pytest.mark.parametrize("assessment", ["plausible", "supported", "rejected"])
def test_cited_hypotheses_are_recorded_without_claiming_an_established_cause(assessment):
    collector = evidence_tools.EvidenceCollector(
        evidence=[Evidence(id="tool:1", source="tool", summary="Observed correlation")]
    )
    report = finalize_report(
        ReportDraft.model_validate(
            {
                "hypotheses": [
                    {
                        "title": "A deployment caused the errors",
                        "assessment": assessment,
                        "evidence_ids": ["tool:1"],
                    }
                ]
            }
        ),
        collector,
    )
    assert report.summary == (f"Hypothesis ({assessment}): A deployment caused the errors [tool:1]")
    assert report.outcome == "inconclusive"
    assert report.cause == ""
    assert report.hypotheses[0].assessment == assessment


@pytest.mark.parametrize("section", ["cause", "impact", "next_steps", "previous_occurrence"])
def test_supported_sections_supply_a_meaningful_headline_without_summary(section):
    collected = evidence_tools.EvidenceCollector(
        evidence=[Evidence(id="tool:1", source="tool", summary="Observed condition")]
    )
    report = finalize_report(
        ReportDraft.model_validate(
            {section: [{"text": "The measured condition", "evidence_ids": ["tool:1"]}]}
        ),
        collected,
    )
    assert "The measured condition [tool:1]" in report.summary
    assert report.outcome == "findings"


def test_malformed_and_foreign_headers_are_not_evidence():
    collector = evidence_tools.EvidenceCollector()
    text = (
        f"{CONTEXT_MARKER}{{not json}}\n"
        f"{CONTEXT_MARKER}{json.dumps({'evidence_id': 'tool:x', 'source_url': 'https://x'})}\n"
        f"{CONTEXT_MARKER}{json.dumps({'evidence_id': 'slack:9', 'source_url': 'http://insecure'})}"
    )
    assert context_evidence(text, collector) == 1
    assert [(item.id, item.url) for item in collector.evidence] == [("slack:9", "")]


def test_digest_fields_strip_citations_without_erasing_bracketed_findings():
    """Only evidence references are citation noise; a bracketed errno is part of the finding."""
    from openswe.incidents.models import IncidentReport
    from openswe.incidents.report import digest_fields

    def report(summary: str) -> IncidentReport:
        return IncidentReport(summary=summary, impact="Impact remains unverified.")

    first = report("Connection failed [Errno 111] [slack:1.0]")
    changed = report("Connection failed [Errno 104] [slack:2.0]")

    assert digest_fields(first) != digest_fields(changed)
    assert digest_fields(first)["summary"] == "Connection failed [Errno 111]"
