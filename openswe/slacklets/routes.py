"""Dashboard API for pull request slacklets: the message as Slack shows it, and its command log."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.slack.blocks import block_payload
from openswe.slacklets.store import PullRequestSlacklet
from openswe.threads.summary import assert_thread_readable
from openswe.transcript.snapshot import load_access
from openswe.utils.json_types import JsonObject

router = APIRouter(tags=["slacklets"])


class SlackletCommand(BaseModel):
    seq: int
    op: str
    summary: str
    command: JsonObject
    created_at: datetime | None


class Slacklet(BaseModel):
    id: UUID
    pull_request_url: str
    pull_request_key: str
    blocks: list[dict[str, Any]]
    commands: list[SlackletCommand]


@router.get("/slacklets/{slacklet_id}")
async def api_slacklet(slacklet_id: UUID, session: dict[str, str] = SESSION_DEP) -> Slacklet:
    """A slacklet in a thread the session may read: the blocks Slack shows, then every command."""
    slacklet = await PullRequestSlacklet.get(slacklet_id)
    thread_id = slacklet.slack_thread.thread_id if slacklet else None
    metadata = await load_access(thread_id) if thread_id else None
    if slacklet is None or metadata is None:
        raise HTTPException(404, "slacklet_not_found")
    assert_thread_readable(metadata, session["sub"], session.get("email"))
    records = await slacklet.commands()
    if not records:
        raise HTTPException(404, "slacklet_not_found")
    last = records[-1][0]
    return Slacklet(
        id=slacklet.id,
        pull_request_url=last.view.url,
        pull_request_key=last.view.key,
        blocks=block_payload(last.view.blocks(seq=last.seq, last=last.command)),
        commands=[
            SlackletCommand(
                seq=entry.seq,
                op=entry.command.op,
                summary=entry.command.summary(),
                command=record.command,
                created_at=record.created_at,
            )
            for entry, record in records
        ],
    )
