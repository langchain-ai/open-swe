"""Compact Slack findings; the incident document retains the complete report."""

import html
import re
from typing import Any
from urllib.parse import urlsplit

from agent.incidents.models import IncidentReport


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

        value = re.sub(r"\[([^\[\]]+)\]", citation, value)
        value = re.sub(r"\s+([.,;:])", r"\1", " ".join(value.split()))
        # Count escaped characters without cutting through entities.
        if len(html.escape(value, quote=False)) > limit:
            remaining = limit - 1
            prefix = []
            for character in value:
                remaining -= len(html.escape(character, quote=False))
                if remaining < 0:
                    break
                prefix.append(character)
            value = "".join(prefix).rsplit(" ", 1)[0].rstrip(".,;:") + "…"
        return html.escape(value, quote=False)

    heading = {"answer": "Investigation answer", "completion": "Incident complete"}.get(
        reason, "Investigation update"
    )
    summary = compact(text, 2400 if reason == "answer" else 800)
    sections = [summary or "No evidence-backed conclusion was established."]
    if report.impact and reason != "answer":
        sections.append("*Impact:* " + compact(report.impact, 300))
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": heading}},
        *(
            {"type": "section", "text": {"type": "mrkdwn", "text": section, "verbatim": True}}
            for section in sections
        ),
    ]
    footer = []
    if report.gaps:
        footer.append("Limited evidence: " + compact(report.gaps[0], 200))
    urls = list(
        dict.fromkeys(evidence[item].url for item in cited if _safe_url(evidence[item].url))
    )[:3]
    if urls:
        footer.append(
            "Sources: "
            + " ".join(f"<{html.escape(url, quote=False)}|[{i}]>" for i, url in enumerate(urls, 1))
        )
    if incident_url and _safe_url(incident_url):
        footer.append(f"<{html.escape(incident_url, quote=False)}|Full report &amp; postmortem>")
    if footer:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "\n".join(footer), "verbatim": True}],
            }
        )
    return "\n\n".join([heading, *sections, *footer]), blocks
