"""Render model-authored incident summaries with the shared Slack renderer."""

import html
import re
from typing import Any
from urllib.parse import urlsplit

from agent.incidents.models import IncidentReport
from agent.slack.blocks import block_payload
from agent.slack.markdown import markdown_blocks


def _safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme in {"https", "http"}
            and url.hostname
            and not url.username
            and not url.password
            and len(value) <= 500
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
    message = report.slack_message if reason != "completion" else ""
    message = message or text or "No evidence-backed conclusion was established."
    message = re.sub(
        r"<[@#!][^<>]*>",
        lambda match: html.escape(match.group(0), quote=False),
        message,
    )
    if incident_url and _safe_url(incident_url):
        message += f"\n\n[View investigation](<{incident_url}>)"
    blocks = markdown_blocks(message)
    if blocks is None:
        raise ValueError("Incident report exceeds Slack block limits")
    return message, block_payload(blocks)
