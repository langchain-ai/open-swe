"""Searching ``human_text`` for one reader, by meaning and by words at once.

Nearest-neighbour search over the embeddings finds paraphrases; full-text search
finds the exact names, error codes and identifiers an embedding blurs. Each ranks
its own candidates and reciprocal rank fusion merges them, so a row strong in
either list rises without the two scores ever being compared.

Visibility is decided per row when it is read, from the channel or thread as it
is now: a channel made private, or a thread deleted, stops showing its text.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import BaseModel
from sqlalchemy import Row, text

from openswe.database.postgres import read_only_transaction
from openswe.human_text.embeddings import Embedder, vector_literal
from openswe.slack.payloads import SlackChannelPayload
from openswe.threads.summary import thread_is_readable
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

type Source = Literal["slack", "open_swe"]

MAX_LIMIT: Final = 50
# Candidates each ranking contributes; a reader's filter can drop most of them.
_POOL: Final = 200
# The usual reciprocal rank fusion constant: it damps the gap between the top few ranks.
_RRF_K: Final = 60
_MAX_TEXT_CHARS: Final = 4000

_SOURCE_FILTERS: Final[dict[Source, str]] = {
    "slack": "h.slack_channel_id IS NOT NULL",
    "open_swe": "h.thread_id IS NOT NULL",
}


@dataclass(frozen=True, kw_only=True)
class Reader:
    """Whoever sees the results, which decides the private text among them.

    A reader with no ``login`` is an audience of more than one person, such as
    everyone who can read a shared thread: it sees public channels and public
    threads only.
    """

    login: str | None = None
    email: str | None = None
    slack_user_id: str = ""

    @classmethod
    async def person(cls, login: str, email: str | None) -> Self:
        user = await User.for_login("github", login)
        return cls(login=login, email=email, slack_user_id=user.slack_user_id if user else "")

    def may_read(self, row: Row[tuple[object, ...]]) -> bool:
        if row.slack_channel_id is not None:
            # Slack never tells Open SWE who else is in a private channel or DM,
            # so only the author is known to have seen it.
            return SlackChannelPayload.of(row.channel_payload).is_public or (
                bool(self.slack_user_id) and row.slack_user_id == self.slack_user_id
            )
        metadata = row.thread_metadata if isinstance(row.thread_metadata, dict) else {}
        return thread_is_readable(metadata, self.login, self.email)


class HumanTextHit(BaseModel):
    source: Source
    text: str
    text_truncated: bool
    sent_at: datetime
    author_login: str | None
    slack_user_id: str | None
    slack_channel: str | None
    thread_title: str | None
    url: str | None
    score: float

    @classmethod
    def from_row(cls, row: Row[tuple[object, ...]]) -> Self:
        body: str = row.body
        if row.slack_channel_id is not None:
            source: Source = "slack"
            url = _slack_permalink(row.slack_channel_id, row.slack_ts, row.slack_thread_ts)
        else:
            source = "open_swe"
            url = dashboard_thread_url(row.thread_id)
        return cls(
            source=source,
            text=body[:_MAX_TEXT_CHARS],
            text_truncated=len(body) > _MAX_TEXT_CHARS,
            sent_at=row.sent_at,
            author_login=row.author_login,
            slack_user_id=row.slack_user_id,
            slack_channel=row.channel_name or None,
            thread_title=row.thread_title,
            url=url,
            score=float(row.score),
        )


def _slack_permalink(channel_id: str, ts: str, thread_ts: str | None) -> str:
    url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    return f"{url}?thread_ts={thread_ts}&cid={channel_id}" if thread_ts else url


async def _query_embedding(query: str) -> str | None:
    """The query's vector, or ``None`` to search by words alone."""
    embedder = Embedder.configured()
    if embedder is None:
        return None
    try:
        [vector] = await embedder.embed([query])
    except Exception:
        logger.warning("Embedding a human text query failed; searching by words", exc_info=True)
        return None
    return vector_literal(vector)


async def search(
    query: str,
    reader: Reader,
    *,
    limit: int = 20,
    source: Source | None = None,
    author: str | None = None,
) -> list[HumanTextHit]:
    """The rows best matching ``query`` that ``reader`` may see, best first.

    ``author`` narrows to one person's writing, by GitHub login.
    """
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, MAX_LIMIT))
    filters = ["TRUE"]
    params: JsonObject = {"query": query, "pool": _POOL, "rrf_k": _RRF_K}
    if source is not None:
        filters.append(_SOURCE_FILTERS[source])
    if author:
        filters.append(
            "h.created_by IN (SELECT user_id FROM user_identity "
            "WHERE provider = 'github' AND lower(login) = lower(:author))"
        )
        params["author"] = author.strip().removeprefix("@")
    where = " AND ".join(filters)
    rankings = [
        f"""
        SELECT id, row_number() OVER (ORDER BY score DESC) AS rank FROM (
            SELECT h.id, ts_rank_cd(h.body_search, q) AS score
            FROM human_text h, websearch_to_tsquery('english', :query) AS q
            WHERE h.body_search @@ q AND {where}
            ORDER BY score DESC
            LIMIT :pool
        ) matched
        """
    ]
    if (embedding := await _query_embedding(query)) is not None:
        params["embedding"] = embedding
        rankings.append(
            f"""
            SELECT id, row_number() OVER (ORDER BY distance) AS rank FROM (
                SELECT h.id, h.embedding <=> CAST(:embedding AS vector) AS distance
                FROM human_text h
                WHERE h.embedding IS NOT NULL AND {where}
                ORDER BY distance
                LIMIT :pool
            ) nearest
            """
        )
    statement = f"""
        WITH ranked AS ({" UNION ALL ".join(rankings)}),
        fused AS (
            SELECT id, sum(1.0 / (:rrf_k + rank)) AS score FROM ranked GROUP BY id
        )
        SELECT
            h.slack_channel_id, h.slack_ts, h.slack_thread_ts, h.slack_user_id,
            h.thread_id, h.body, h.sent_at, fused.score,
            t.title AS thread_title, t.metadata AS thread_metadata,
            c.name AS channel_name, c.payload AS channel_payload,
            (
                SELECT login FROM user_identity
                WHERE user_id = h.created_by AND provider = 'github'
                ORDER BY last_seen_at DESC NULLS LAST
                LIMIT 1
            ) AS author_login
        FROM fused
        JOIN human_text h ON h.id = fused.id
        LEFT JOIN thread t ON t.thread_id = h.thread_id
        LEFT JOIN slack_channel c ON c.id = h.slack_channel_id
        ORDER BY fused.score DESC, h.sent_at DESC
    """
    async with read_only_transaction() as conn:
        # The index scan stops at ``ef_search`` rows; without iterating past
        # filtered-out rows it would return fewer than the pool.
        await conn.execute(text(f"SET LOCAL hnsw.ef_search = {_POOL}"))
        await conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = (await conn.execute(text(statement), params)).all()
    return [HumanTextHit.from_row(row) for row in rows if reader.may_read(row)][:limit]
