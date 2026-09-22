"""The author's steering of an Open SWE pull request, and what the reviewer made of it.

Open SWE already records every human turn: ``thread_message`` holds the text and
the ``<input-message>`` envelope that says whether a person typed it or the
platform generated it, and ``pull_request_thread`` maps a PR back to the agent
threads that produced it. This module reads those turns so they can be put in
front of the reviewer.

Deciding which of them *changed the pull request* needs the code, not the
conversation — a reply saying "drop the retry wrapper" only counts if the
wrapper is actually gone. The reviewer is the one process that has the diff and
the checked-out repo, so it makes that call itself through ``record_guidance``
and the points land in its thread metadata beside its findings.
"""

import logging
from datetime import datetime
from typing import Any, Literal, TypedDict, cast

from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError as LangGraphSDKNotFoundError
from pydantic import BaseModel
from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres
from agent.github.pull_requests import PullRequest
from agent.input_messages import input_message_text, message_sender_id
from agent.review.findings import ReviewerThreadMissingError, get_thread_metadata

logger = logging.getLogger(__name__)

GuidanceKind = Literal["correction", "constraint", "direction", "preference"]

GUIDANCE_CAP = 8
MAX_FOLLOW_UPS = 40
MAX_MESSAGE_CHARS = 4_000


class GuidancePoint(TypedDict, total=False):
    """One steering turn the reviewer verified against the final change."""

    summary: str
    quote: str
    kind: GuidanceKind
    file: str
    start_line: int | None
    author: str
    occurred_at: str


class HumanTurn(BaseModel):
    """One message a person actually typed into a thread behind this PR."""

    thread_id: str
    message_id: str
    author: str
    text: str
    created_at: datetime


class SteeringHistory(BaseModel):
    """The opening ask, and every human turn after it."""

    request: HumanTurn
    follow_ups: list[HumanTurn]


async def _human_turns(thread_ids: list[str]) -> list[HumanTurn]:
    """Every message a person typed across these threads, oldest first.

    ``role = 'human'`` covers platform-generated wake-ups too — they ride the
    same channel — so the envelope decides: only a ``kind="human"``
    ``<input-message>`` was typed by someone.
    """
    if not thread_ids:
        return []
    statement = text(
        """
        SELECT thread_id, message_id, text, sender, created_at
        FROM thread_message
        WHERE thread_id = ANY(:thread_ids) AND role = 'human'
        ORDER BY created_at, message_id
        """
    ).bindparams(bindparam("thread_ids", type_=ARRAY(Text)))
    async with postgres.snapshot_transaction() as conn:
        rows = (await conn.execute(statement, {"thread_ids": thread_ids})).mappings().all()
    turns: list[HumanTurn] = []
    for row in rows:
        raw = row["text"]
        if not isinstance(raw, str) or message_sender_id(raw, kind="human") is None:
            continue
        body = (input_message_text(raw) or "").strip()
        if not body:
            continue
        sender = row["sender"] if isinstance(row["sender"], dict) else {}
        login = sender.get("login")
        turns.append(
            HumanTurn(
                thread_id=row["thread_id"],
                message_id=row["message_id"],
                author=login if isinstance(login, str) and login else "unknown",
                text=body[:MAX_MESSAGE_CHARS],
                created_at=row["created_at"],
            )
        )
    return turns


async def load_steering_history(owner: str, repo: str, pr_number: int) -> SteeringHistory | None:
    """This PR's human turns, or ``None`` when Open SWE did not write it.

    A PR the agent produced in one pass has an opening request and nothing
    after it, which is no steering at all.
    """
    pull_request = await PullRequest.get(owner, repo, pr_number)
    if pull_request is None:
        return None
    thread_ids = await pull_request.linked_threads()
    if not thread_ids:
        return None
    turns = await _human_turns(thread_ids)
    if len(turns) < 2:
        return None
    return SteeringHistory(request=turns[0], follow_ups=turns[1:][-MAX_FOLLOW_UPS:])


def coerce_guidance(value: Any) -> list[GuidancePoint]:
    if not isinstance(value, list):
        return []
    return [cast(GuidancePoint, item) for item in value if isinstance(item, dict)]


async def list_guidance(thread_id: str) -> list[GuidancePoint]:
    """The points the reviewer recorded on its thread."""
    metadata = await get_thread_metadata(thread_id)
    return coerce_guidance(metadata.get("guidance"))


async def replace_guidance(thread_id: str, points: list[GuidancePoint]) -> None:
    client = get_client()
    try:
        await client.threads.update(thread_id=thread_id, metadata={"guidance": points})
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc


async def append_guidance(thread_id: str, point: GuidancePoint) -> list[GuidancePoint]:
    """Add one point, replacing an earlier record of the same quote.

    A re-review starts from the same messages, so the reviewer re-derives points
    it already recorded; keying on the quote keeps the newest verdict rather
    than accumulating a duplicate per push.
    """
    points = await list_guidance(thread_id)
    quote = point.get("quote", "")
    kept = [existing for existing in points if existing.get("quote") != quote]
    kept.append(point)
    trimmed = kept[-GUIDANCE_CAP:]
    await replace_guidance(thread_id, trimmed)
    return trimmed
