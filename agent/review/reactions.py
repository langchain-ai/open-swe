"""Reconcile native GitHub review reactions without changing approval judgments."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Literal, TypedDict

from langgraph_sdk import get_client
from pydantic import BaseModel, Field
from sqlalchemy import text

from agent import database
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.http import GITHUB_API_BASE, GITHUB_GRAPHQL, github_client, github_request
from agent.review.risk import ASSESSMENTS, ReactionSummary, RiskAssessment
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)
CRON_TASK = "review_reactions"
BATCH_SIZE = 25
REGISTRATION_INTERVAL_SECONDS = 300

_QUERY = """
query ReviewReactions($id: ID!, $cursor: String) {
  node(id: $id) {
    ... on PullRequestReview {
      databaseId body commit { oid }
      reactions(first: 100, after: $cursor) {
        nodes { content user { login } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class ReactionVote(BaseModel):
    login: str
    rating: Literal["helpful", "unhelpful", "conflicting"]


class ReactionSnapshot(BaseModel):
    assessment_id: str
    github_review_id: int
    votes: list[ReactionVote]
    synced_at: str = Field(default_factory=now_iso)


class SyncPosition(BaseModel):
    offset: int = Field(default=0, ge=0)


SNAPSHOTS = TypedStore(["review_assessment_reactions"], ReactionSnapshot)
POSITION = TypedStore(["review_reaction_sync"], SyncPosition)


class _User(BaseModel):
    login: str


class _Reaction(BaseModel):
    content: str
    user: _User | None


class _PageInfo(BaseModel):
    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(alias="endCursor")


class _Reactions(BaseModel):
    nodes: list[_Reaction]
    page_info: _PageInfo = Field(alias="pageInfo")


class _Commit(BaseModel):
    oid: str


class _Review(BaseModel):
    database_id: int = Field(alias="databaseId")
    body: str
    commit: _Commit
    reactions: _Reactions


class _Data(BaseModel):
    node: _Review | None


class _Response(BaseModel):
    data: _Data | None = None
    errors: list[object] | None = None


async def get_reaction_summary(assessment_id: str, login: str) -> ReactionSummary:
    snapshot = await SNAPSHOTS.get(assessment_id)
    if snapshot is None:
        return ReactionSummary()
    return ReactionSummary(
        helpful=sum(vote.rating == "helpful" for vote in snapshot.votes),
        unhelpful=sum(vote.rating == "unhelpful" for vote in snapshot.votes),
        viewer_rating=next(
            (vote.rating for vote in snapshot.votes if vote.login == login.lower()), None
        ),
        synced_at=snapshot.synced_at,
    )


async def sync_assessment_reactions(record: RiskAssessment, token: str) -> None:
    if record.github_review_id is None or not record.publication_complete:
        return
    votes: dict[str, set[str]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    async with github_client(token=token) as client:
        response = await github_request(
            client,
            "GET",
            f"{GITHUB_API_BASE}/repos/{record.repo_full_name}/pulls/{record.pr_number}/reviews/{record.github_review_id}",
        )
        response.raise_for_status()
        review: object = response.json()
        node_id = review.get("node_id") if isinstance(review, dict) else None
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("GitHub review has no node ID")
        for _ in range(100):
            response = await github_request(
                client,
                "POST",
                GITHUB_GRAPHQL,
                json={"query": _QUERY, "variables": {"id": node_id, "cursor": cursor}},
            )
            response.raise_for_status()
            payload = _Response.model_validate(response.json())
            node = payload.data.node if payload.data else None
            if payload.errors or node is None:
                raise ValueError("GitHub reaction response was incomplete")
            if (
                node.database_id != record.github_review_id
                or node.commit.oid != record.head_sha
                or f"<!-- open-swe-review-risk id={record.id} -->" not in node.body
            ):
                raise ValueError("GitHub reactions do not match the published assessment")
            for reaction in node.reactions.nodes:
                if reaction.user is None or reaction.content not in {"THUMBS_UP", "THUMBS_DOWN"}:
                    continue
                login = reaction.user.login.lower()
                if login and not login.endswith("[bot]"):
                    votes.setdefault(login, set()).add(reaction.content)
            page = node.reactions.page_info
            if not page.has_next_page:
                break
            cursor = page.end_cursor
            if not cursor or cursor in seen_cursors:
                raise ValueError("GitHub reaction pagination did not advance")
            seen_cursors.add(cursor)
        else:
            raise ValueError("GitHub reaction pagination was incomplete")
    snapshot = ReactionSnapshot(
        assessment_id=record.id,
        github_review_id=record.github_review_id,
        votes=[
            ReactionVote(
                login=login,
                rating="conflicting"
                if len(contents) > 1
                else "helpful"
                if "THUMBS_UP" in contents
                else "unhelpful",
            )
            for login, contents in sorted(votes.items())
        ],
    )
    # Replace only after every page succeeds, so removals reconcile without erasing votes on outages.
    await SNAPSHOTS.put(record.id, snapshot)


@asynccontextmanager
async def _sync_lock(purpose: str) -> AsyncIterator[bool]:
    async with database.transaction() as conn:
        result = await conn.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": f"open-swe:review-reactions:{purpose}"},
        )
        yield bool(result.scalar_one())


async def ensure_reaction_sync_cron() -> None:
    async with _sync_lock("registration") as acquired:
        if not acquired:
            return
        client = get_client()
        crons = await client.crons.search(metadata={"kind": CRON_TASK}, limit=100)
        ids = sorted(cron["cron_id"] for cron in crons)
        if ids:
            for duplicate in ids[1:]:
                await client.crons.delete(duplicate)
            return
        await client.crons.create(
            "scheduler",
            schedule="*/5 * * * *",
            input={"task": CRON_TASK},
            metadata={"kind": CRON_TASK},
            timezone="UTC",
        )


async def _maintain_reaction_sync_cron() -> None:
    while True:
        try:
            await ensure_reaction_sync_cron()
        except Exception:
            logger.exception("Failed to ensure review reaction collection cron")
        await asyncio.sleep(REGISTRATION_INTERVAL_SECONDS)


@asynccontextmanager
async def reaction_sync_lifecycle() -> AsyncIterator[None]:
    # Run after startup yields, when the local LangGraph API can accept requests.
    task = asyncio.create_task(_maintain_reaction_sync_cron())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class SyncResult(TypedDict):
    synced: int
    failed: int


async def sync_review_reactions() -> SyncResult:
    async with _sync_lock("collection") as acquired:
        if not acquired:
            return SyncResult(synced=0, failed=0)
        return await _sync_review_reactions()


async def _sync_review_reactions() -> SyncResult:
    position = await POSITION.get("default") or SyncPosition()
    records = await ASSESSMENTS.search(
        filter={"publication_complete": True}, limit=BATCH_SIZE, offset=position.offset
    )
    result = SyncResult(synced=0, failed=0)
    tokens: dict[str, str] = {}
    for record in records:
        try:
            token = tokens.get(record.repo_full_name)
            if token is None:
                owner, repo = record.repo_full_name.split("/", 1)
                installation = await get_github_app_installation_id_for_repo(owner, repo)
                if installation is None:
                    raise ValueError("No GitHub App installation for reaction sync")
                token = await get_github_app_installation_token(
                    installation_id=installation,
                    repositories=[repo],
                    permissions={"pull_requests": "read"},
                )
                if not token:
                    raise ValueError("No GitHub App token for reaction sync")
                tokens[record.repo_full_name] = token
            await sync_assessment_reactions(record, token)
            result["synced"] += 1
        except Exception:
            logger.exception(
                "Failed to sync review assessment reactions",
                extra={"assessment_id": record.id, "repo_full_name": record.repo_full_name},
            )
            result["failed"] += 1
    await POSITION.put(
        "default",
        SyncPosition(offset=position.offset + len(records) if len(records) == BATCH_SIZE else 0),
    )
    return result
