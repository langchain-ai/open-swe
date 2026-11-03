from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from openswe.review_guide import routes
from openswe.review_guide.walk import OnScreen, Walk
from openswe.walkthrough.lines import LineSet


@pytest.mark.parametrize("clicked", ["reader", "someone else", "stale chunk"])
async def test_dashboard_next_approves_only_the_readers_chunk_on_screen(
    monkeypatch: pytest.MonkeyPatch, clicked: str
) -> None:
    reader_id = uuid4()
    walk = Walk(
        head_sha="head-1",
        on_screen=OnScreen(chunk_id="c1", title="One", lines=LineSet(), message_ts="m1"),
    )
    guide = SimpleNamespace(closed=False, user_id=reader_id, walk=walk)
    monkeypatch.setattr(routes.ReviewGuideSession, "get", AsyncMock(return_value=guide))
    monkeypatch.setattr(
        routes.User,
        "for_login",
        AsyncMock(
            return_value=SimpleNamespace(id=uuid4() if clicked == "someone else" else reader_id)
        ),
    )
    advance = AsyncMock()
    monkeypatch.setattr(routes, "advance", advance)
    body = routes.InteractiveReviewNext(message_ts="m0" if clicked == "stale chunk" else "m1")

    if clicked == "reader":
        await routes.api_interactive_review_next("thread-1", body, session={"sub": "alice"})
        advance.assert_awaited_once_with(guide, "m1")
        return
    with pytest.raises(HTTPException) as raised:
        await routes.api_interactive_review_next("thread-1", body, session={"sub": "alice"})
    assert raised.value.status_code == (403 if clicked == "someone else" else 409)
    advance.assert_not_awaited()
