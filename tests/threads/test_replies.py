from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from openswe.database import postgres
from openswe.tasks import service
from openswe.threads import replies
from openswe.threads.replies import ThreadReply
from openswe.webhooks.event_matches import EventMatch
from openswe.webhooks.event_subscriptions import EventSubscription

_REQUESTER = "3b8f4848-78b4-45d5-a617-3f4610feff4d"
_TARGET = "86186b55-1999-52e2-bf4b-ca3de907043e"


async def test_first_run_holding_the_message_answers_its_requester_once(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = await ThreadReply.request(_REQUESTER, _TARGET)
    dispatched = {"type": "human", "content": "Fix login", "id": reply.message_id}
    checkpoint: list[dict[str, object]] = [{"values": {"messages": []}}]
    client = SimpleNamespace(
        threads=SimpleNamespace(get_history=AsyncMock(return_value=checkpoint))
    )
    deliver = AsyncMock()
    monkeypatch.setattr(replies, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        service, "recipient_config", AsyncMock(return_value={"thread_id": _REQUESTER})
    )
    monkeypatch.setattr(EventSubscription, "deliver_to", deliver)
    payload = {"values": {"messages": [dispatched, {"type": "ai", "content": "Fixed in #12"}]}}

    # Steered after the run's last model call: a later run takes the message in.
    await ThreadReply.answer(_TARGET, "earlier-run", "success", payload)
    checkpoint[0] = {"values": {"messages": [dispatched]}}
    # Interrupted runs leave the reply to the run that interrupted them.
    await ThreadReply.answer(_TARGET, "interrupted-run", "interrupted", payload)
    await ThreadReply.answer(_TARGET, "run", "success", payload)
    await ThreadReply.answer(_TARGET, "later-run", "success", payload)

    async with postgres.session() as session:
        owed = list(
            await session.scalars(select(EventMatch).where(EventMatch.thread_id == _REQUESTER))
        )
    assert [(match.source, match.subscription_id) for match in owed] == [("thread", reply.id)]
    assert "Fixed in #12" in owed[0].content
    deliver.assert_awaited_once_with(_REQUESTER, "enqueue")
