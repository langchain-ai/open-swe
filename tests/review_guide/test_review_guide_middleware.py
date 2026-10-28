from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from openswe.review_guide import middleware
from openswe.review_guide.middleware import ReviewGuideMiddleware


async def test_a_closed_walkthrough_tells_the_model_once_that_its_rules_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its rules stay in the conversation, so only a later note can retire them."""
    closed = MagicMock(closed=True)
    monkeypatch.setattr(middleware.ReviewGuideSession, "get", AsyncMock(return_value=closed))
    guide = ReviewGuideMiddleware(thread_id="t", approve_ts="")

    ended = await guide.abefore_agent({"messages": []}, MagicMock())
    assert ended is not None
    (note,) = ended["messages"]
    assert "walkthrough has ended" in note.content

    told = {"messages": [HumanMessage(content=note.content)]}
    assert await guide.abefore_agent(told, MagicMock()) is None
