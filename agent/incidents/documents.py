"""Latest agent postmortem summaries, ready to read and copy elsewhere."""

import re
from typing import Any

from fastapi import HTTPException

from agent.incidents import service
from agent.incidents.document_models import DocumentKind, DocumentRevision, IncidentHistory
from agent.incidents.models import Incident, IncidentReport
from agent.store import TypedStore, get_value, now_iso, put_value

LEGACY_REVISIONS = TypedStore(["incidents", "document_revisions"], DocumentRevision)
HISTORY = TypedStore(["incidents", "history"], IncidentHistory)
SUMMARIES = ["incidents", "summaries"]


async def require_access(incident_id: str) -> Incident:
    record = await service.INVESTIGATIONS.get(incident_id)
    policy = await service.get_policy()
    history = await HISTORY.get(incident_id)
    if (
        not record
        or record.workspace_id != policy.workspace_id
        or record.reason == "code_channel"
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


async def _legacy_revisions(incident_id: str, kind: DocumentKind) -> list[DocumentRevision]:
    return sorted(
        await LEGACY_REVISIONS.search_all(filter={"incident_id": incident_id, "kind": kind}),
        key=lambda revision: revision.revision,
        reverse=True,
    )


def _revision_view(revision: DocumentRevision, record: Incident) -> dict[str, Any]:
    result = revision.model_dump()
    live_urls = (
        {message.source_url for message in record.messages if not message.deleted}
        if not record.expired
        else set()
    )
    references = []
    for reference in revision.evidence:
        available = bool(reference.url and reference.url in live_urls)
        references.append(
            {
                "id": reference.id,
                "source": reference.source,
                "url": reference.url if available else "",
                "available": available,
            }
        )
        if reference.url and not available:
            result["markdown"] = result["markdown"].replace(reference.url, "[evidence unavailable]")
    result["evidence"] = references
    return result


def _linked_markdown(markdown: str, evidence: list[dict[str, Any]]) -> str:
    sources = []
    for index, item in enumerate(evidence, 1):
        if item.get("url"):
            markdown = markdown.replace(f"[{item['id']}]", f"[{index}]")
            sources.append(f"[{index}]: <{item['url']}>")
    return markdown + ("\n\n" + "\n".join(sources) if sources else "")


async def _saved_markdown(record: Incident) -> str | None:
    summary = await get_value(SUMMARIES, record.id)
    markdown = summary["markdown"] if summary else None
    if markdown is None:
        legacy = await _legacy_revisions(record.id, "postmortem")
        if legacy:
            view = _revision_view(legacy[0], record)
            markdown = _linked_markdown(view["markdown"], view["evidence"])
    return markdown


async def document_context(incident_id: str) -> dict[str, Any]:
    record = await require_access(incident_id)
    markdown = await _saved_markdown(record)
    if markdown:
        live_urls = (
            {message.source_url for message in record.messages if not message.deleted}
            if not record.expired
            else set()
        )
        markdown = re.sub(
            r"https://(?:[a-zA-Z0-9-]+\.)?slack\.com/archives/[^\s<>)]*",
            lambda match: match[0] if match[0] in live_urls else "[evidence unavailable]",
            markdown,
        )
    return {
        "incident_id": incident_id,
        "postmortem": {"markdown": markdown} if markdown is not None else None,
    }


def _findings(report: IncidentReport) -> str:
    lines = [f"### Findings — {report.created_at}", "", report.summary]
    if report.impact:
        lines.extend(["", f"Impact: {report.impact}"])
    if report.next_steps:
        lines.extend(["", "Suggested next steps:", *[f"- {item}" for item in report.next_steps]])
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
    try:
        offset = int(cursor or "0")
        if offset < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    items = []
    for history in sorted(
        await HISTORY.search_all(), key=lambda item: item.updated_at, reverse=True
    ):
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
    limit = max(1, min(limit, 100))
    return {
        "items": items[offset : offset + limit],
        "next_cursor": str(offset + limit) if len(items) > offset + limit else None,
    }
