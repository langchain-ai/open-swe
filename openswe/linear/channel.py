"""Routes for MDA's Linear channel.

The channel receives Linear's webhooks in the MDA deployment and forwards each
delivery here exactly as Linear sent it. The backend checks Linear's signature
again, so only genuine deliveries start runs, and decides what they do.
"""

import hmac
import json
import time
from typing import TypedDict
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from openswe.config import ENV
from openswe.linear.session import LinearSessionTurn, PreparedRun
from openswe.remote_runtime.client import MDA_SERVICE_SECRET_HEADER
from openswe.webhooks import common
from openswe.webhooks.event_log import EventLog, EventRefs

# Linear signs webhookTimestamp with the body; an older delivery is a replay.
_MAX_DELIVERY_AGE_MS = 60_000
# Linear's deliveries are a few kilobytes; anything near this is not one.
_MAX_BODY_CHARS = 1_000_000


def _require_mda_channel(request: Request) -> None:
    """Only the MDA deployment, which shares the service secret, calls these routes."""
    expected = ENV.MDA_SERVICE_SECRET.optional()
    provided = request.headers.get(MDA_SERVICE_SECRET_HEADER, "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid service credential")


router = APIRouter(prefix="/linear/channel", dependencies=[Depends(_require_mda_channel)])


class ForwardedDelivery(BaseModel):
    """A Linear delivery the channel received, forwarded as Linear sent it."""

    thread_id: UUID
    delivery_id: str = Field(min_length=1, max_length=200)
    raw_body: str = Field(max_length=_MAX_BODY_CHARS)
    signature: str = Field(min_length=1, max_length=200)

    def payload(self) -> dict[str, object]:
        """The delivery's payload, once Linear's signature and its age check out."""
        raw = self.raw_body.encode()
        if not common.verify_linear_signature(raw, self.signature, common.LINEAR_WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Invalid signature")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid payload")
        timestamp = payload.get("webhookTimestamp")
        if not isinstance(timestamp, int) or abs(time.time() * 1000 - timestamp) > (
            _MAX_DELIVERY_AGE_MS
        ):
            raise HTTPException(status_code=401, detail="Stale webhook timestamp")
        return payload

    async def record(self, request: Request, payload: dict[str, object]) -> None:
        """Log the delivery and wake the threads that listen for Linear events."""
        raw = self.raw_body.encode()
        await EventLog.record(
            request,
            raw,
            "linear",
            event_type=str(payload.get("type") or ""),
            delivery_id=self.delivery_id,
            refs=EventRefs.linear(raw),
        )


class PreparedLinearRun(TypedDict):
    """The run the channel starts, or None to start nothing."""

    run: PreparedRun | None


@router.post("/runs")
async def prepare_linear_run(request: Request, delivery: ForwardedDelivery) -> PreparedLinearRun:
    """Prepare the run an agent session turn starts on the channel's thread."""
    payload = delivery.payload()
    await delivery.record(request, payload)
    turn = LinearSessionTurn.parse(payload)
    if turn is None:
        return {"run": None}
    return {"run": await turn.prepare_run(str(delivery.thread_id))}


async def _launch_automations(payload: dict[str, object], delivery_id: str) -> None:
    from openswe.schedules.store import launch_linear_automations

    try:
        await launch_linear_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Linear automations failed for a delivery", extra={"linear_delivery": delivery_id}
        )


@router.post("/events")
async def receive_linear_event(
    request: Request, delivery: ForwardedDelivery, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Log a Linear event that starts no conversation, and run the automations it triggers."""
    payload = delivery.payload()
    await delivery.record(request, payload)
    if payload.get("type") == "Issue" and payload.get("action") in {"create", "update"}:
        background_tasks.add_task(_launch_automations, payload, delivery.delivery_id)
    return {"status": "accepted"}
