"""Where a person redirected the agent while it was building the pull request.

Open SWE already records every human turn: ``thread_message`` holds the text,
the sender and the envelope that says whether a person typed it or the platform
generated it, and ``pull_request_thread`` maps a PR back to the agent threads
that produced it. What is missing is the reading — a run of thirty turns buries
the two moments where the author said "no, not like that" among the noise.

One extraction pass over the human follow-ups keeps the turns that changed the
agent's course and drops the rest. The result is written to the store keyed by
the pull request, so the reviewer's prompt and the dashboard card show the same
points, and a re-review with unchanged follow-ups reuses the extraction instead
of paying for it again.
"""

import logging
from datetime import datetime
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import ARRAY, Text, bindparam, text

from agent.database import postgres
from agent.github.pull_requests import PullRequest
from agent.input_messages import input_message_text, message_sender_id
from agent.prompts import load_prompt
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

GuidanceKind = Literal["correction", "constraint", "direction", "preference"]

MAX_GUIDANCE_POINTS = 8
MAX_FOLLOW_UPS = 40
MAX_MESSAGE_CHARS = 4_000
MAX_EXTRACTION_CHARS = 60_000

_EXTRACTION_SYSTEM_PROMPT = load_prompt("reviewer/author-guidance-extraction.md")


class HumanTurn(BaseModel):
    """One message a person actually typed into a thread behind this PR."""

    thread_id: str
    message_id: str
    author: str
    text: str
    created_at: datetime


class GuidancePoint(BaseModel):
    summary: str = Field(description="One line, past tense, naming what the author changed")
    quote: str = Field(description="The words from the message that carry the instruction")
    kind: GuidanceKind
    author: str = ""
    occurred_at: str = ""


class _Extraction(BaseModel):
    points: list[GuidancePoint] = Field(default_factory=list)


class AuthorGuidance(BaseModel):
    """The extracted points, and enough of the input to know when they are stale."""

    owner: str
    repo: str
    pr_number: int
    points: list[GuidancePoint] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)
    follow_up_count: int = 0
    last_message_id: str = ""
    generated_at: str = ""

    def covers(self, follow_ups: list[HumanTurn]) -> bool:
        return self.follow_up_count == len(follow_ups) and self.last_message_id == (
            follow_ups[-1].message_id if follow_ups else ""
        )


GUIDANCE = TypedStore(("pr_author_guidance",), AuthorGuidance)


def _key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner}/{repo}#{pr_number}".lower()


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


def _extraction_input(request: HumanTurn, follow_ups: list[HumanTurn]) -> str:
    lines = [
        "<original_request>",
        request.text,
        "</original_request>",
        "",
        "<follow_ups>",
    ]
    for index, turn in enumerate(follow_ups, start=1):
        lines.append(
            f'<message index="{index}" author="{turn.author}" at="{turn.created_at.isoformat()}">'
        )
        lines.append(turn.text)
        lines.append("</message>")
    lines.append("</follow_ups>")
    return "\n".join(lines)[:MAX_EXTRACTION_CHARS]


def _attribute(points: list[GuidancePoint], follow_ups: list[HumanTurn]) -> list[GuidancePoint]:
    """Stamp each point with the message its quote came from.

    The model is told to quote verbatim, so the quote locates the turn; a point
    whose quote matches nothing keeps the empty attribution rather than being
    pinned to the wrong person.
    """
    attributed: list[GuidancePoint] = []
    for point in points[:MAX_GUIDANCE_POINTS]:
        needle = point.quote.strip().strip('"').lower()
        source = next((turn for turn in follow_ups if needle and needle in turn.text.lower()), None)
        attributed.append(
            point.model_copy(
                update={
                    "author": source.author if source else "",
                    "occurred_at": source.created_at.isoformat() if source else "",
                }
            )
        )
    return attributed


async def load_author_guidance(owner: str, repo: str, pr_number: int) -> AuthorGuidance | None:
    return await GUIDANCE.get(_key(owner, repo, pr_number))


async def refresh_author_guidance(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    model: BaseChatModel,
) -> AuthorGuidance | None:
    """Extract this PR's guidance points, reusing the stored ones when unchanged.

    ``None`` when the PR is not Open SWE's work, or when nobody steered it: a PR
    the agent produced in one pass has no guidance to show.
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
    request, follow_ups = turns[0], turns[1:][-MAX_FOLLOW_UPS:]

    stored = await load_author_guidance(owner, repo, pr_number)
    if stored is not None and stored.covers(follow_ups):
        return stored

    structured = model.with_structured_output(_Extraction)
    result = await structured.ainvoke(
        [
            SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=_extraction_input(request, follow_ups)),
        ],
        # Empty callbacks: this pass must not stream its tokens into the
        # reviewer run that triggered it.
        config={"callbacks": [], "run_name": "pr-author-guidance"},
    )
    if not isinstance(result, _Extraction):
        logger.warning(
            "Author guidance extraction returned an unusable result",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
        )
        return None
    guidance = AuthorGuidance(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        points=_attribute(result.points, follow_ups),
        thread_ids=thread_ids,
        follow_up_count=len(follow_ups),
        last_message_id=follow_ups[-1].message_id,
        generated_at=now_iso(),
    )
    await GUIDANCE.put(_key(owner, repo, pr_number), guidance)
    return guidance
