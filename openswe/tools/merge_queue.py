"""Recovery operations authorized by the current run's bound queue thread."""

from uuid import UUID

from pydantic import JsonValue

from openswe.audit_logs.tools import audit_tool
from openswe.merge_queue.diagnosis import QueueRecovery
from openswe.merge_queue.recovery import RecoveryProposal


async def merge_queue_recovery_status() -> dict[str, JsonValue]:
    """Read the bound recovery sequence and its pending notifications."""
    service = await QueueRecovery.current()
    history, state = await service.bound()
    return await service.context(history, state)


@audit_tool()
async def merge_queue_recover(proposal: RecoveryProposal) -> dict[str, JsonValue]:
    """Submit one bounded recovery proposal for the current failed candidate."""
    service = await QueueRecovery.current()
    return await service.act(proposal)


@audit_tool()
async def merge_queue_resume(pause_id: UUID, reason: str) -> dict[str, JsonValue]:
    """Resume only this thread's current outage pause, after observing recovery."""
    service = await QueueRecovery.current()
    return await service.resume(pause_id, reason)


@audit_tool()
async def merge_queue_follow_up(minutes: int) -> dict[str, JsonValue]:
    """Schedule this investigation's next durable follow-up in 1–60 minutes."""
    service = await QueueRecovery.current()
    return await service.follow_up(minutes)


@audit_tool()
async def merge_queue_notify(event_id: str) -> dict[str, JsonValue]:
    """Deliver one confirmed recovery outcome to this thread's Slack conversation."""
    service = await QueueRecovery.current()
    return await service.notify(event_id)
