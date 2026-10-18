"""Dashboard API for slacklets: the message as Slack shows it, and the command log behind it."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.slack.blocks import block_payload
from openswe.slacklets.kinds import KINDS
from openswe.slacklets.store import Slacklet
from openswe.threads.summary import assert_thread_readable
from openswe.transcript.snapshot import load_access
from openswe.utils.json_types import JsonObject

router = APIRouter(tags=["slacklets"])


class SlackletCommandOut(BaseModel):
    seq: int
    op: str
    summary: str
    command: JsonObject
    created_at: datetime | None


class SlackletOut(BaseModel):
    id: UUID
    kind: str
    subject: str
    blocks: list[dict[str, Any]]
    commands: list[SlackletCommandOut]


async def readable_slacklet(slacklet_id: UUID, login: str, email: str | None) -> Slacklet:
    """The slacklet, if it is in a thread ``login`` may read; 404 otherwise."""
    slacklet = await Slacklet.get(slacklet_id)
    thread_id = slacklet.slack_thread.thread_id if slacklet else None
    metadata = await load_access(thread_id) if thread_id else None
    if slacklet is None or metadata is None:
        raise HTTPException(404, "slacklet_not_found")
    assert_thread_readable(metadata, login, email)
    return slacklet


@router.get("/slacklets/{slacklet_id}")
async def api_slacklet(slacklet_id: UUID, session: dict[str, str] = SESSION_DEP) -> SlackletOut:
    """A slacklet in a thread the session may read: the blocks Slack shows, then every command."""
    slacklet = await readable_slacklet(slacklet_id, session["sub"], session.get("email"))
    kind = KINDS.get(slacklet.kind)
    history = await kind.history(slacklet) if kind else []
    if not history:
        raise HTTPException(404, "slacklet_not_found")
    last = history[-1][0]
    return SlackletOut(
        id=slacklet.id,
        kind=slacklet.kind,
        subject=slacklet.subject,
        blocks=block_payload(last.state.blocks(seq=last.seq, op=last.command.op)),
        commands=[
            SlackletCommandOut(
                seq=entry.seq,
                op=entry.command.op,
                summary=entry.command.summary(),
                command=record.command,
                created_at=record.created_at,
            )
            for entry, record in history
        ],
    )
