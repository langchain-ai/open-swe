from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.inbox.snoozes import MAX_SNOOZE_MS, InboxSnoozes, is_inbox_item_key
from openswe.store import now_ms

router = APIRouter()


class SnoozeRequest(BaseModel):
    key: str
    until_ms: int


def _item_key(key: str) -> str:
    if not is_inbox_item_key(key):
        raise HTTPException(400, "unknown inbox item")
    return key


@router.get("/inbox/snoozes")
async def api_list_inbox_snoozes(
    session: dict[str, Any] = SESSION_DEP,
) -> list[dict[str, object]]:
    return [snooze.as_dict() for snooze in await InboxSnoozes(session["sub"]).current()]


@router.put("/inbox/snoozes")
@audit_endpoint
async def api_snooze_inbox_item(
    body: SnoozeRequest,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, object]:
    now = now_ms()
    if not now < body.until_ms <= now + MAX_SNOOZE_MS:
        raise HTTPException(400, "snooze must end within 90 days")
    snooze = await InboxSnoozes(session["sub"]).snooze(_item_key(body.key), body.until_ms)
    return snooze.as_dict()


@router.delete("/inbox/snoozes", status_code=204)
@audit_endpoint
async def api_wake_inbox_item(
    key: str = Query(),
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await InboxSnoozes(session["sub"]).wake(_item_key(key))
    return Response(status_code=204)
