"""Natural-language search with Jev: one yes/no request per candidate, a page at a time."""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from langchain_typesafe import ClassifierResponse, Noul, TypeSafeClassifier
from pydantic import BaseModel

from openswe.utils.jev import jev_classifier

logger = logging.getLogger(__name__)

JEV_SEARCH_PAGE_SIZE = 10
JEV_SEARCH_TIMEOUT_SECONDS = 10.0
JEV_MATCH_THRESHOLD = 0.5


@dataclass(frozen=True)
class JevMatch[ItemT]:
    item: ItemT
    relevance: float


@dataclass(frozen=True)
class JevBatch[ItemT]:
    """The matches in one judged page, and how many candidates the search has judged so far."""

    matches: list[JevMatch[ItemT]]
    scanned: int


class JevSearch:
    """Judges candidates against one query. Each candidate is its own Jev request, since
    Jev answers every question in a request against a single state."""

    def __init__(self, query: str, subject: str, *, instructions: str) -> None:
        self.query = query
        self.subject = subject
        self._question = Noul(instructions=instructions)
        self._today = datetime.now(UTC).date().isoformat()

    async def stream[ItemT](
        self, pages: AsyncIterable[Sequence[ItemT]], card: Callable[[ItemT], BaseModel]
    ) -> AsyncIterator[JevBatch[ItemT]]:
        """Judge each page's candidates in parallel and yield its matches before the next page."""
        async with jev_classifier(JEV_SEARCH_TIMEOUT_SECONDS) as classifier:
            scanned = 0
            async for page in pages:
                scores = await self._judge(classifier, [card(item) for item in page])
                scanned += len(page)
                yield JevBatch(
                    matches=[
                        JevMatch(item, score)
                        for item, score in zip(page, scores, strict=True)
                        if score is not None and score >= JEV_MATCH_THRESHOLD
                    ],
                    scanned=scanned,
                )

    async def rank[ItemT](
        self, items: Sequence[ItemT], card: Callable[[ItemT], BaseModel]
    ) -> list[JevMatch[ItemT]]:
        """Every match among ``items``, most likely first."""
        matches: list[JevMatch[ItemT]] = []
        async for batch in self.stream(_pages(items), card):
            matches.extend(batch.matches)
        return sorted(matches, key=lambda match: match.relevance, reverse=True)

    async def _judge(
        self, classifier: TypeSafeClassifier, cards: Sequence[BaseModel]
    ) -> list[float | None]:
        if not cards:
            return []
        async with asyncio.timeout(JEV_SEARCH_TIMEOUT_SECONDS):
            # abatch returns exceptions in place, but is typed `list[Output]`:
            # https://github.com/langchain-ai/langchain/issues/41276
            responses: list[ClassifierResponse | Exception] = await classifier.abatch(  # ty: ignore[invalid-assignment]
                [
                    {
                        "state": {
                            "query": self.query,
                            "today": self._today,
                            self.subject: card.model_dump(mode="json", exclude_none=True),
                        },
                        "questions": {"match": self._question},
                    }
                    for card in cards
                ],
                config={"tags": ["nostream"]},
                return_exceptions=True,
            )
        failures = [response for response in responses if isinstance(response, Exception)]
        if len(failures) == len(responses):
            raise failures[0]
        if failures:
            logger.warning(
                "Jev could not judge some search candidates",
                extra={"subject": self.subject, "failed": len(failures), "page": len(responses)},
                exc_info=failures[0],
            )
        return [
            answer.noul
            if isinstance(response, ClassifierResponse)
            and (answer := response.nouls.get("match")) is not None
            else None
            for response in responses
        ]


async def _pages[ItemT](items: Sequence[ItemT]) -> AsyncIterator[Sequence[ItemT]]:
    for start in range(0, len(items), JEV_SEARCH_PAGE_SIZE):
        yield items[start : start + JEV_SEARCH_PAGE_SIZE]
