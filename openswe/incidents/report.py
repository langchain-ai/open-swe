"""Report contract for incident turns: prompt, draft schema, citations, and context evidence."""

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from openswe.incidents.evidence_tools import EvidenceCollector, redact, source_url
from openswe.incidents.models import Evidence, Hypothesis, IncidentReport
from openswe.prompts import prompt

CONTEXT_MARKER = "INCIDENT_CONTEXT "
_CONTEXT_HEADER = re.compile(r"INCIDENT_CONTEXT (\{[^\n]*\})")
_CONTEXT_HEADER_LINE = re.compile(r"^INCIDENT_CONTEXT \{[^\n]*\}\n?")


def context_message(text: str) -> str:
    """The Slack message under an incident context header, or ``text`` when it has none."""
    return _CONTEXT_HEADER_LINE.sub("", text, count=1)


# Only a bracket group made entirely of evidence references, as finalize_report appends them
# ("[slack:1.0]", "[slack:1.0, tool:9fd2…]"). A bracket in the finding itself, such as
# "[Errno 111]", carries meaning and has to survive into the digest.
_EVIDENCE_REF = r"[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9._:-]+"
_CITATION = re.compile(rf"\s*\[{_EVIDENCE_REF}(?:\s*,\s*{_EVIDENCE_REF})*\]")


class Claim(BaseModel):
    text: str = Field(max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class ReportDraft(BaseModel):
    summary: list[Claim] = Field(default_factory=list, max_length=6)
    problem: list[Claim] = Field(default_factory=list, max_length=4)
    previous_occurrence: list[Claim] = Field(default_factory=list, max_length=4)
    impact: list[Claim] = Field(default_factory=list, max_length=6)
    cause: list[Claim] = Field(default_factory=list, max_length=4)
    next_steps: list[Claim] = Field(default_factory=list, max_length=3)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=10)
    questions: list[str] = Field(default_factory=list, max_length=5)

    def references(self) -> set[str]:
        return {
            evidence_id
            for claim in (
                *self.summary,
                *self.problem,
                *self.previous_occurrence,
                *self.impact,
                *self.cause,
                *self.next_steps,
                *self.hypotheses,
            )
            for evidence_id in claim.evidence_ids
        }


INCIDENT_PROMPT = prompt("incidents/report")


def digest_fields(report: IncidentReport) -> dict[str, Any]:
    """The parts of a report that state its conclusion, for deciding whether to post again.

    Citations, retrieved evidence, and checked sources are deliberately excluded: every turn
    cites the newest channel message, so including them would make each turn look new and
    defeat the check. Wording changes still read as a new conclusion, which is why an
    unprompted post also has to clear a minimum interval.
    """

    def bare(value: str) -> str:
        return " ".join(_CITATION.sub("", value).split())

    return {
        "summary": bare(report.summary),
        "problem": bare(report.problem),
        "previous_occurrence": bare(report.previous_occurrence),
        "impact": bare(report.impact),
        "cause": bare(report.cause),
        "outcome": report.outcome,
        "next_steps": [bare(step) for step in report.next_steps],
        "hypotheses": [
            [bare(hypothesis.title), hypothesis.assessment] for hypothesis in report.hypotheses
        ],
        "questions": [bare(question) for question in report.questions],
    }


def context_evidence(text: str, collector: EvidenceCollector) -> int:
    """Register `slack:<ts>` evidence for every incident context header in `text`."""
    known = {item.id for item in collector.evidence}
    added = 0
    for match in _CONTEXT_HEADER.finditer(text):
        try:
            header = json.loads(match.group(1))
        except ValueError:
            continue
        evidence_id = header.get("evidence_id") if isinstance(header, dict) else None
        if (
            not isinstance(evidence_id, str)
            or not evidence_id.startswith("slack:")
            or evidence_id in known
        ):
            continue
        collector.evidence.append(
            Evidence(
                id=evidence_id,
                source="slack",
                url=source_url(str(header.get("source_url") or "")),
                summary="Message in the incident channel",
            )
        )
        known.add(evidence_id)
        added += 1
    return added


def finalize_report(draft: ReportDraft, collector: EvidenceCollector) -> IncidentReport:
    known_ids = {evidence.id for evidence in collector.evidence}
    dropped = False

    def valid_refs(ids: list[str]) -> bool:
        nonlocal dropped
        valid = bool(ids) and all(evidence_id in known_ids for evidence_id in ids)
        if not valid:
            dropped = True
        return valid

    def render(claims: list[Claim]) -> list[str]:
        return [
            f"{redact(claim.text, 1200)} [{', '.join(dict.fromkeys(claim.evidence_ids))}]"
            for claim in claims
            if claim.text.strip() and valid_refs(claim.evidence_ids)
        ]

    summary = " ".join(render(draft.summary))
    problem = " ".join(render(draft.problem))
    previous_occurrence = " ".join(render(draft.previous_occurrence))
    impact = " ".join(render(draft.impact))
    cause = " ".join(render(draft.cause))
    next_steps = render(draft.next_steps)
    hypotheses = [
        Hypothesis(
            title=redact(hypothesis.title, 1200),
            assessment=hypothesis.assessment,
            evidence_ids=list(dict.fromkeys(hypothesis.evidence_ids)),
        )
        for hypothesis in draft.hypotheses
        if hypothesis.title.strip() and valid_refs(hypothesis.evidence_ids)
    ]
    headline = (
        summary
        or problem
        or cause
        or impact
        or previous_occurrence
        or (f"Suggested next step: {next_steps[0]}" if next_steps else "")
    )
    limitations = " ".join(
        redact(text, 500) for text in (*draft.gaps, *draft.questions) if text.strip()
    )
    if not headline and not hypotheses and not limitations:
        raise ValueError(
            "Report has no supported findings. Nothing was recorded or posted. "
            "Retry record_incident_report with nonempty claims citing evidence IDs returned "
            "by this incident's tools or context. Re-read missing evidence before citing it; "
            "if evidence is unavailable, report concrete gaps or questions instead of guesses."
        )
    gaps = collector.gaps + [redact(gap, 500) for gap in draft.gaps if gap.strip()]
    if dropped:
        gaps.append("Claims with missing or unknown evidence citations were omitted.")
    hypothesis_headline = ""
    if hypotheses:
        first = hypotheses[0]
        hypothesis_headline = (
            f"Hypothesis ({first.assessment}): {first.title} [{', '.join(first.evidence_ids)}]"
        )
    return IncidentReport(
        summary=headline or hypothesis_headline or f"Investigation incomplete: {limitations}",
        problem=problem,
        # A skipped recurrence check is worth showing: it is the section responders rely on
        # most, and an empty one would otherwise read as "this has never happened before".
        previous_occurrence=previous_occurrence or "No previous-occurrence check was recorded.",
        impact=impact or "Impact remains unverified.",
        cause=cause,
        next_steps=next_steps,
        outcome="findings" if headline else "inconclusive",
        hypotheses=hypotheses,
        evidence=collector.evidence,
        checked=list(dict.fromkeys(collector.checked)),
        gaps=list(dict.fromkeys(gaps)),
        questions=[redact(question, 500) for question in draft.questions],
    )
