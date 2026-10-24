"""Dashboard API for searching what people wrote."""

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.human_text.search import MAX_LIMIT, HumanTextHit, Reader, Source, search

router = APIRouter(tags=["human-text"])


class HumanTextSearchResults(BaseModel):
    results: list[HumanTextHit]


@router.get("/human-text/search")
async def api_search_human_text(
    q: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=20, ge=1, le=MAX_LIMIT),
    source: Source | None = None,
    author: str | None = Query(default=None, max_length=100),
    session: dict[str, Any] = SESSION_DEP,
) -> HumanTextSearchResults:
    reader = await Reader.person(session["sub"], session.get("email"))
    return HumanTextSearchResults(
        results=await search(q, reader, limit=limit, source=source, author=author)
    )
