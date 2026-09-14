"""Citation validation and context evidence for incident reports."""

import json

import pytest

from agent.incidents import evidence_tools
from agent.incidents.models import Evidence
from agent.incidents.report import CONTEXT_MARKER, ReportDraft, context_evidence, finalize_report


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


@pytest.mark.parametrize("references", [[], ["invented"]])
def test_report_without_supported_summary_remains_inconclusive(references):
    report = finalize_report(
        ReportDraft.model_validate(
            {"summary": [{"text": "Everything is healthy", "evidence_ids": references}]}
        ),
        evidence_tools.EvidenceCollector(),
    )
    assert report.outcome == "inconclusive"
    assert "Everything is healthy" not in report.summary
    assert report.gaps


def _envelope(text: str) -> str:
    return f'<input-message sender="slack:U1" surface="slack" kind="human">\n<content>{text}</content>\n</input-message>'


def test_context_headers_become_slack_evidence_once():
    collector = evidence_tools.EvidenceCollector()
    header = json.dumps(
        {
            "evidence_id": "slack:1.2",
            "source_url": "https://slack.com/archives/C1/p12",
            "author": "Datadog",
        }
    )
    text = _envelope(f"{CONTEXT_MARKER}{header}\nLatency alert")

    assert context_evidence(text, collector) == 1
    assert context_evidence(text, collector) == 0
    assert collector.evidence[0].id == "slack:1.2"
    assert collector.evidence[0].url == "https://slack.com/archives/C1/p12"
    assert collector.evidence[0].source == "slack"


def test_malformed_and_foreign_headers_are_not_evidence():
    collector = evidence_tools.EvidenceCollector()
    text = (
        f"{CONTEXT_MARKER}{{not json}}\n"
        f"{CONTEXT_MARKER}{json.dumps({'evidence_id': 'tool:x', 'source_url': 'https://x'})}\n"
        f"{CONTEXT_MARKER}{json.dumps({'evidence_id': 'slack:9', 'source_url': 'http://insecure'})}"
    )
    assert context_evidence(text, collector) == 1
    assert [(item.id, item.url) for item in collector.evidence] == [("slack:9", "")]
