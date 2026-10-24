"""Embedding ``human_text`` rows, in the background, through the same gateway as chat models.

Rows are written without vectors; a job every minute embeds whatever is still
missing one, so a burst of messages or a model outage only delays search.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self
from uuid import UUID

from sqlalchemy import text

from openswe.config import ENV
from openswe.database import postgres
from openswe.utils.gateway import gateway_endpoint, gateway_env_default

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

EMBED_JOB: Final = "human_text_embed"
EMBED_SCHEDULE: Final = "* * * * *"

MODEL: Final = "text-embedding-3-small"
DIMENSIONS: Final = 1536
# Well inside the model's 8,191-token input, so no input needs counting first.
_MAX_INPUT_CHARS: Final = 6000
_BATCH: Final = 128
# A run stops taking batches after this long, so it finishes before the next minute's.
_RUN_BUDGET_SECONDS: Final = 45.0


def vector_literal(vector: Sequence[float]) -> str:
    """``vector`` in pgvector's text form, for ``CAST(:value AS vector)``."""
    return "[" + ",".join(repr(value) for value in vector) + "]"


@dataclass(frozen=True)
class Embedder:
    client: AsyncOpenAI

    @classmethod
    def configured(cls) -> Self | None:
        """The embedder this deployment can reach, or ``None`` without OpenAI access."""
        # Imported here: the webapp must start without the model SDKs.
        from openai import AsyncOpenAI

        if gateway_env_default() and (endpoint := gateway_endpoint("openai")) is not None:
            return cls(AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key))
        api_key = ENV.OPENAI_API_KEY.optional()
        if not api_key:
            return None
        return cls(AsyncOpenAI(base_url=ENV.OPENAI_BASE_URL.optional(), api_key=api_key))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=MODEL,
            input=[value[:_MAX_INPUT_CHARS] for value in texts],
            dimensions=DIMENSIONS,
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


async def embed_pending() -> int:
    """Embed rows still missing a vector, for up to one run's budget; how many were embedded."""
    embedder = Embedder.configured()
    if embedder is None:
        return 0
    deadline = time.monotonic() + _RUN_BUDGET_SECONDS
    embedded = 0
    while time.monotonic() < deadline:
        async with postgres.transaction() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, body FROM human_text WHERE embedding IS NULL "
                        "ORDER BY id LIMIT :limit"
                    ),
                    {"limit": _BATCH},
                )
            ).all()
        if not rows:
            break
        pending: list[tuple[UUID, str]] = [(row.id, row.body) for row in rows]
        vectors = await embedder.embed([body for _, body in pending])
        async with postgres.transaction() as conn:
            # An edit made while the batch was out keeps its NULL, for the next batch.
            await conn.execute(
                text(
                    "UPDATE human_text SET embedding = CAST(:embedding AS vector) "
                    "WHERE id = :id AND body = :body AND embedding IS NULL"
                ),
                [
                    {"id": row_id, "body": body, "embedding": vector_literal(vector)}
                    for (row_id, body), vector in zip(pending, vectors, strict=True)
                ],
            )
        embedded += len(pending)
        if len(pending) < _BATCH:
            break
    if embedded:
        logger.info("Embedded human text", extra={"human_text_embedded": embedded})
    return embedded
