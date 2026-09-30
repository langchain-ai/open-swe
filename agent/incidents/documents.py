"""Latest agent postmortem summaries, ready to read and copy elsewhere."""

from typing import Any

from fastapi import HTTPException

from agent.incidents import service
from agent.incidents.document_models import IncidentHistory
from agent.incidents.models import Incident, IncidentReport
from agent.store import TypedStore, get_value, now_iso, put_value

HISTORY = TypedStore(["incidents", "history"], IncidentHistory)
SUMMARIES = ["incidents", "summaries"]


async def require_access(incident_id: str) -> Incident:
    record = await service.INCIDENTS.get(incident_id)
    policy = await service.get_policy()
    history = await HISTORY.get(incident_id)
    if (
        not record
        or record.workspace_id != policy.workspace_id
        or record.channel_id in policy.excluded_channel_ids
        or (
            history
            and (history.workspace_id, history.channel_id)
            != (record.workspace_id, record.channel_id)
        )
    ):
        raise HTTPException(404, "Incident not found")
    if not await service.readable(record, policy, raise_on_unavailable=True):
        raise HTTPException(404, "Incident not found")
    return record


async def preserve_metadata(record: Incident) -> None:
    previous = await HISTORY.get(record.id)
    await HISTORY.put(
        record.id,
        IncidentHistory(
            id=record.id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            channel_name=record.channel_name or (previous.channel_name if previous else ""),
            title=record.title or (previous.title if previous else record.channel_name),
            status=record.status,
            created_at=record.created_at,
            updated_at=now_iso(),
        ),
    )


def _linked_markdown(markdown: str, evidence: list[dict[str, Any]]) -> str:
    sources = []
    for index, item in enumerate(evidence, 1):
        if item.get("url"):
            markdown = markdown.replace(f"[{item['id']}]", f"[{index}]")
            sources.append(f"[{index}]: <{item['url']}>")
    return markdown + ("\n\n" + "\n".join(sources) if sources else "")


async def _saved_markdown(record: Incident) -> str | None:
    summary = await get_value(SUMMARIES, record.id)
    return summary["markdown"] if summary else None


async def document_context(incident_id: str) -> dict[str, Any]:
    record = await require_access(incident_id)
    markdown = await _saved_markdown(record)
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
    await preserve_metadata(record)
    await put_value(SUMMARIES, record.id, {"markdown": markdown})


async def search_history(
    q: str | None = None, *, limit: int = 25, cursor: str | None = None
) -> dict[str, Any]:
    items = []
    for history in await HISTORY.search_all():
        try:
            record = await require_access(history.id)
        except HTTPException:
            continue
        markdown = await _saved_markdown(record)
        if (
            q
            and q.casefold()
            not in f"{history.title} {history.channel_name} {markdown or ''}".casefold()
        ):
            continue
        item = history.model_dump(exclude={"workspace_id", "channel_id"})
        item["status"] = record.status
        items.append(item)
    return service.paginate(service.sort_newest_first(items), cursor, limit)
