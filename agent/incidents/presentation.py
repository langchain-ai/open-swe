"""Compact Slack findings; the incident document retains the complete report."""

import html
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from agent.incidents.models import IncidentReport
from agent.slack.blocks import block_payload
from agent.slack.markdown import markdown_blocks

# The investigation format responders read top to bottom, with a per-section character
# budget that keeps the whole message inside Slack's block limits.
_SECTIONS = (
    ("problem", "Problem", 500),
    ("previous_occurrence", "Previous occurrence", 500),
    ("impact", "Impact", 400),
    ("cause", "Cause", 500),
)


def _safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme in {"https", "http"}
            and url.hostname
            and not url.username
            and not url.password
            and len(html.escape(value, quote=False)) <= 500
            and not any(c.isspace() or c in "<>|" for c in value)
        )
    except ValueError:
        return False


def _investigation(report: IncidentReport, compact: Callable[[str, int], str]) -> list[str]:
    """The named investigation sections, skipping any the turn had nothing to say about."""
    sections = [
        f"**{label}**\n{value}"
        for field, label, limit in _SECTIONS
        if (value := compact(getattr(report, field), limit))
    ]
    steps = [step for step in (compact(item, 300) for item in report.next_steps[:3]) if step]
    if steps:
        sections.append(
            "**Steps to solve**\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
        )
    return sections


def report_message(
    report: IncidentReport,
    text: str,
    incident_url: str | None,
    *,
    reason: str = "findings",
) -> tuple[str, list[dict[str, Any]]]:
    evidence = {item.id: item for item in report.evidence}
    cited: list[str] = []

    def compact(value: str, limit: int) -> str:
        def citation(match: re.Match[str]) -> str:
            ids = [item.strip() for item in match.group(1).split(",")]
            if not any(item in evidence for item in ids):
                return match.group(0)
            cited.extend(item for item in ids if item in evidence)
            return ""

        value = re.sub(r"\[([^\[\]]+)\](?!\()", citation, value)
        value = re.sub(r"\s+([.,;:])", r"\1", " ".join(value.split()))

        value = re.sub(
            r"<[@#!][^<>]*>",
            lambda match: html.escape(match.group(0), quote=False),
            value,
        )
        if len(value) > limit:
            value = value[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
        return value

    heading = {
        "answer": "Investigation answer",
        "completion": "Incident complete",
        "findings": "Investigation",
    }.get(reason, "Investigation update")
    if reason == "findings":
        # Without a problem section the headline is the only statement of the finding; the
        # fallback previous-occurrence and impact sections must not stand in for it.
        summary = "" if report.problem else compact(text, 800)
        sections = [f"**{heading}**\n{summary}" if summary else f"**{heading}**"]
        sections.extend(_investigation(report, compact))
    else:
        summary = compact(text, 2400 if reason == "answer" else 800)
        sections = [f"**{heading}**\n{summary or 'No evidence-backed conclusion was established.'}"]
    footer = []
    urls = list(
        dict.fromkeys(evidence[item].url for item in cited if _safe_url(evidence[item].url))
    )[:3]
    if urls:
        footer.append(
            "Sources: " + " ".join(f"[Source {i}]({url})" for i, url in enumerate(urls, 1))
        )
    if incident_url and _safe_url(incident_url):
        footer.append(f"[View investigation]({incident_url})")
    message = "\n\n".join([*sections, *footer])
    blocks = markdown_blocks(message)
    if blocks is None:
        raise ValueError("Incident report exceeds Slack block limits")
    return message, block_payload(blocks)
