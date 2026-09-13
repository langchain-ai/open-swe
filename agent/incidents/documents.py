"""Immutable documents; only the channel worker applies queued edits."""

import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException

from agent.incidents import service
from agent.incidents.document_models import (
    DocumentKind,
    DocumentOperation,
    DocumentReference,
    DocumentRevision,
    IncidentHistory,
)
from agent.incidents.models import Incident, IncidentReport, Receipt
from agent.store import TypedStore, now_iso

REVISIONS = TypedStore(["incidents", "document_revisions"], DocumentRevision)
OPERATIONS = TypedStore(["incidents", "document_operations"], DocumentOperation)
HISTORY = TypedStore(["incidents", "history"], IncidentHistory)


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


async def _revisions(incident_id: str, kind: DocumentKind) -> list[DocumentRevision]:
    return sorted(
        await REVISIONS.search_all(filter={"incident_id": incident_id, "kind": kind}),
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


def _operation_view(operation: DocumentOperation) -> dict[str, Any]:
    return operation.model_dump(exclude={"content_hash"})


def _pending_operation(receipt: Receipt) -> DocumentOperation:
    return DocumentOperation(
        id=receipt.id,
        incident_id=receipt.payload["incident_id"],
        kind=receipt.payload["kind"],
        expected_revision=receipt.payload["expected_revision"],
        content_hash=receipt.content_hash,
        created_at=datetime.fromtimestamp(receipt.received_at, UTC).isoformat(),
    )


async def document_context(incident_id: str) -> dict[str, Any]:
    record = await require_access(incident_id)
    result: dict[str, Any] = {"incident_id": incident_id}
    for kind in ("postmortem", "status_page_draft"):
        revisions = await _revisions(incident_id, kind)
        result[kind] = _revision_view(revisions[0], record) if revisions else None
    operations = {
        operation.id: operation
        for operation in await OPERATIONS.search_all(filter={"incident_id": incident_id})
    }
    for receipt in await service.RECEIPTS.search_all(filter={"channel_id": record.channel_id}):
        if (
            receipt.kind == "document_edit"
            and receipt.workspace_id == record.workspace_id
            and receipt.payload.get("incident_id") == incident_id
            and receipt.id not in operations
        ):
            operations[receipt.id] = _pending_operation(receipt)
    result["operations"] = [
        _operation_view(operation)
        for operation in sorted(operations.values(), key=lambda op: op.created_at, reverse=True)[
            :20
        ]
    ]
    return result


async def list_revisions(incident_id: str, kind: DocumentKind) -> dict[str, Any]:
    record = await require_access(incident_id)
    return {"items": [_revision_view(item, record) for item in await _revisions(incident_id, kind)]}


async def get_revision(incident_id: str, kind: DocumentKind, revision: int) -> dict[str, Any]:
    record = await require_access(incident_id)
    item = await REVISIONS.get(f"{incident_id}:{kind}:{revision}")
    if item is None:
        raise HTTPException(404, "Document revision not found")
    return _revision_view(item, record)


async def get_operation(incident_id: str, operation_id: str) -> dict[str, Any]:
    record = await require_access(incident_id)
    operation = await OPERATIONS.get(operation_id)
    if operation and operation.incident_id == incident_id:
        return _operation_view(operation)
    receipt = await service.RECEIPTS.get(operation_id)
    if (
        receipt
        and receipt.kind == "document_edit"
        and receipt.workspace_id == record.workspace_id
        and receipt.channel_id == record.channel_id
        and receipt.payload.get("incident_id") == incident_id
    ):
        return _operation_view(_pending_operation(receipt))
    raise HTTPException(404, "Document operation not found")


async def submit_edit(
    incident_id: str,
    *,
    kind: DocumentKind,
    markdown: str,
    expected_revision: int,
    request_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    record = await require_access(incident_id)
    payload = {
        "incident_id": incident_id,
        "kind": kind,
        "markdown": markdown,
        "expected_revision": expected_revision,
    }
    operation_id = service.fingerprint([incident_id, "document", actor.get("id"), request_id])
    content_hash = service.fingerprint(payload)
    existing = await OPERATIONS.get(operation_id) or await service.RECEIPTS.get(operation_id)
    if existing:
        if existing.content_hash != content_hash:
            raise HTTPException(409, "Request ID was reused with different content")
        return await get_operation(incident_id, operation_id)
    receipt = Receipt(
        id=operation_id,
        workspace_id=record.workspace_id,
        channel_id=record.channel_id,
        kind="document_edit",
        payload=payload,
        actor=actor,
        received_at=time.time(),
        content_hash=content_hash,
    )
    await service.RECEIPTS.put(operation_id, receipt)
    await service.wake()
    return _operation_view(_pending_operation(receipt))


async def _apply(
    record: Incident,
    operation: DocumentOperation,
    *,
    markdown: str,
    author: str,
    source: Literal["agent", "responder"],
    run_id: str = "",
    evidence: list[DocumentReference] | None = None,
) -> None:
    if await OPERATIONS.get(operation.id):
        return
    revisions = await _revisions(record.id, operation.kind)
    applied = next((item for item in revisions if item.operation_id == operation.id), None)
    current = revisions[0] if revisions else None
    if applied:
        operation.status, operation.revision = "applied", applied.revision
    elif operation.expected_revision != (current.revision if current else 0):
        operation.status = "conflict"
        operation.revision = current.revision if current else 0
        operation.error = "Document changed; reload before saving"
    else:
        revision = DocumentRevision(
            incident_id=record.id,
            kind=operation.kind,
            revision=operation.expected_revision + 1,
            expected_revision=operation.expected_revision,
            markdown=markdown,
            operation_id=operation.id,
            author=author,
            source=source,
            run_id=run_id,
            evidence=evidence if evidence is not None else (current.evidence if current else []),
        )
        await preserve_metadata(record)
        await REVISIONS.put(f"{record.id}:{operation.kind}:{revision.revision}", revision)
        operation.status, operation.revision = "applied", revision.revision
    operation.updated_at = now_iso()
    await OPERATIONS.put(operation.id, operation)


async def process_document_receipt(record: Incident, receipt: Receipt) -> bool:
    if receipt.kind != "document_edit":
        return False
    if (
        receipt.workspace_id != record.workspace_id
        or receipt.channel_id != record.channel_id
        or receipt.payload.get("incident_id") != record.id
    ):
        return True
    operation = _pending_operation(receipt)
    if await OPERATIONS.get(operation.id):
        return True
    try:
        await require_access(record.id)
    except HTTPException as exc:
        if exc.status_code >= 500:
            raise
        operation.status = "rejected"
        operation.error = "Incident access is unavailable"
        await OPERATIONS.put(operation.id, operation)
        return True
    await _apply(
        record,
        operation,
        markdown=receipt.payload["markdown"],
        author=str(receipt.actor.get("id") or "responder"),
        source="responder",
    )
    return True


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


async def update_from_report(
    record: Incident, report: IncidentReport, *, expected_revision: int, run_id: str
) -> None:
    await require_access(record.id)
    operation_id = service.fingerprint([record.id, "report_document", run_id])
    if await OPERATIONS.get(operation_id):
        return
    revisions = await _revisions(record.id, "postmortem")
    current = revisions[0] if revisions else None
    if current:
        markdown = current.markdown + "\n\n" + _findings(report)
    else:
        markdown = (
            f"# {record.title or record.channel_name or 'Incident postmortem'}\n\n"
            f"## Summary\n\n{report.summary}\n\n"
            f"## Impact\n\n{report.impact or 'Unknown.'}\n\n"
            "## Timeline\n\n## Cause\n\nNot yet confirmed.\n\n"
            "## Mitigation\n\n## Resolution\n\n## Follow-ups\n\n"
            "## Investigation findings\n\n" + _findings(report)
        )
    references = {item.id: item for item in current.evidence} if current else {}
    references.update(
        {
            item.id: DocumentReference(id=item.id, source=item.source, url=item.url)
            for item in report.evidence
        }
    )
    operation = DocumentOperation(
        id=operation_id,
        incident_id=record.id,
        kind="postmortem",
        expected_revision=expected_revision,
        content_hash=service.fingerprint([run_id, expected_revision]),
    )
    await _apply(
        record,
        operation,
        markdown=markdown,
        author="agent",
        source="agent",
        run_id=run_id,
        evidence=list(references.values()),
    )


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
        revisions = await _revisions(history.id, "postmortem")
        latest = revisions[0] if revisions else None
        if (
            q
            and q.casefold()
            not in f"{history.title} {history.channel_name} {latest.markdown if latest else ''}".casefold()
        ):
            continue
        item = history.model_dump(exclude={"workspace_id", "channel_id"})
        item["status"] = record.status
        item["postmortem_revision"] = latest.revision if latest else 0
        items.append(item)
    limit = max(1, min(limit, 100))
    return {
        "items": items[offset : offset + limit],
        "next_cursor": str(offset + limit) if len(items) > offset + limit else None,
    }
