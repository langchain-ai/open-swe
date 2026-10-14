"""Latest agent postmortem summaries, ready to read and copy elsewhere."""

from typing import Any

from fastapi import HTTPException

from openswe.incidents import service
from openswe.incidents.document_models import IncidentHistory, IncidentPostmortem
from openswe.incidents.models import Incident, IncidentId, IncidentReport
from openswe.incidents.records import HISTORY, POSTMORTEMS
from openswe.ui_invalidations import Topic


async def require_access(incident_id: IncidentId) -> tuple[str, IncidentHistory]:
    """The incident's thread id and history, if responders may read it now.

    Reads the history document, which outlives the incident's thread.
    """
    policy = await service.get_policy()
    found = await service.INCIDENTS.history(incident_id, policy.workspace_id)
    if found is None or not await service.readable(found[1], policy, raise_on_unavailable=True):
        raise HTTPException(404, "Incident not found")
    return found


def _linked_markdown(markdown: str, evidence: list[dict[str, Any]]) -> str:
    sources = []
    for index, item in enumerate(evidence, 1):
        if item.get("url"):
            markdown = markdown.replace(f"[{item['id']}]", f"[{index}]")
            sources.append(f"[{index}]: <{item['url']}>")
    return markdown + ("\n\n" + "\n".join(sources) if sources else "")


async def _saved_markdown(thread_id: str) -> str | None:
    postmortem = await POSTMORTEMS.get(thread_id)
    return postmortem.markdown if postmortem else None


async def document_context(incident_id: IncidentId) -> dict[str, Any]:
    thread_id, _ = await require_access(incident_id)
    markdown = await _saved_markdown(thread_id)
    return {
        "incident_id": incident_id,
        "postmortem": {"markdown": markdown} if markdown is not None else None,
    }


_SECTIONS = (
    ("problem", "Problem"),
    ("previous_occurrence", "Previous occurrence"),
    ("impact", "Impact"),
    ("cause", "Cause"),
)


def _findings(report: IncidentReport) -> str:
    lines = [f"### Findings — {report.created_at}", "", report.summary]
    for field, label in _SECTIONS:
        if value := getattr(report, field):
            lines.extend(["", f"{label}: {value}"])
    if report.next_steps:
        lines.extend(["", "Steps to solve:", *[f"- {item}" for item in report.next_steps]])
    if report.hypotheses:
        lines.extend(["", "Working hypotheses:"])
    for hypothesis in report.hypotheses:
        citations = " ".join(f"[{item}]" for item in hypothesis.evidence_ids)
        lines.append(f"- {hypothesis.title} ({hypothesis.assessment}) {citations}".rstrip())
    if report.checked:
        lines.extend(["", "Checked:", *[f"- {item}" for item in report.checked]])
    if report.gaps or report.questions:
        lines.extend(
            ["", "Open questions:", *[f"- {item}" for item in [*report.gaps, *report.questions]]]
        )
    if report.evidence:
        lines.extend(["", "Evidence: " + ", ".join(f"[{item.id}]" for item in report.evidence)])
    return "\n".join(lines)


async def update_from_report(record: Incident, report: IncidentReport) -> None:
    await require_access(record.id)
    markdown = f"# {record.title or record.channel_name or 'Incident'}\n\n" + _findings(report)
    markdown = _linked_markdown(markdown, [item.model_dump() for item in report.evidence])
    await service.INCIDENTS.record_history(record)
    await POSTMORTEMS.put(record.thread_id, IncidentPostmortem(markdown=markdown))
    await Topic.INCIDENTS.invalidate(key=record.id)


async def search_history(
    q: str | None = None, *, limit: int = 25, cursor: str | None = None
) -> dict[str, Any]:
    policy = await service.get_policy()
    items = []
    for thread_id, history in await HISTORY.all():
        if history.workspace_id != policy.workspace_id or not await service.readable(
            history, policy
        ):
            continue
        markdown = await _saved_markdown(thread_id)
        if (
            q
            and q.casefold()
            not in f"{history.title} {history.channel_name} {markdown or ''}".casefold()
        ):
            continue
        items.append(history.model_dump(exclude={"workspace_id", "channel_id", "activity"}))
    return service.paginate(service.sort_newest_first(items), cursor, limit)
