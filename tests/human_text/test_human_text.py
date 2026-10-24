"""Which writing becomes searchable, and who can find it.

These run against a real migrated schema: ingestion, the embedding column and
the fused ranking are all SQL.
"""

from collections.abc import Sequence
from uuid import uuid7

import pytest
from openai import AsyncOpenAI
from sqlalchemy import text

from openswe.database import postgres
from openswe.human_text import search
from openswe.human_text.embeddings import DIMENSIONS, Embedder, embed_pending
from openswe.human_text.search import Reader
from openswe.human_text.sources import observe_slack_event
from openswe.input_messages import build_input_messages
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackEvent
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageSender, ThreadCreated, TurnRequested
from openswe.utils.json_types import JsonObject

_PUBLIC_CHANNEL: JsonObject = {
    "id": "CPUBLIC01",
    "name": "general",
    "is_channel": True,
    "is_private": False,
    "is_im": False,
    "is_mpim": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
}
_PRIVATE_CHANNEL: JsonObject = {**_PUBLIC_CHANNEL, "id": "CPRIVATE1", "is_private": True}


class _TopicEmbedder(Embedder):
    """Embeds by topic, so a paraphrase matches with no word in common."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        def vector(value: str) -> list[float]:
            axis = 0 if any(word in value.lower() for word in ("deploy", "ship")) else 1
            return [1.0 if index == axis else 0.0 for index in range(DIMENSIONS)]

        return [vector(value) for value in texts]


@pytest.fixture
def no_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Embedder, "configured", classmethod(lambda cls: None))


async def _thread(visibility: str = "public") -> str:
    thread_id = str(uuid7())
    metadata: JsonObject = {"owner_login": "owner", "visibility": visibility}
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title="A thread",
                    source="dashboard",
                    owner_login="owner",
                    visibility="private" if visibility == "private" else "public",
                    metadata=metadata,
                ),
                actor_kind="user",
            )
        ],
    )
    return thread_id


async def _request(thread_id: str, text_: str, sender_kind: str = "dashboard") -> None:
    turn_id = uuid7()
    await append(
        thread_id,
        [
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id=str(uuid7()),
                    text=text_,
                    sender=MessageSender(login="owner", kind=sender_kind),
                ),
                actor_kind="user",
                turn_id=turn_id,
            )
        ],
    )


def _envelope(body: str, *, surface: str = "web", kind: str = "human") -> str:
    [message] = build_input_messages(
        body, {"sender_id": "github:owner", "surface": surface, "kind": kind}
    )
    content = message["content"]
    assert isinstance(content, str)
    return content


async def _bodies() -> list[str]:
    async with postgres.read_only_transaction() as conn:
        rows = await conn.execute(text("SELECT body FROM human_text ORDER BY sent_at"))
        return list(rows.scalars())


async def _slack(event: JsonObject) -> None:
    parsed = SlackEvent.parse(event)
    assert parsed is not None
    await observe_slack_event(parsed)


async def test_only_what_people_wrote_outside_slack_is_recorded_from_threads(
    registry_db: None,
) -> None:
    thread_id = await _thread()
    await _request(thread_id, _envelope("please fix the flaky login test"))
    await _request(thread_id, _envelope("CI failed on main", surface="automation", kind="system"))
    await _request(thread_id, _envelope("answered from slack", surface="slack"))
    await _request(thread_id, "bare text with no envelope")
    await _request(thread_id, "a prompt typed into Claude Code", sender_kind="claude_code")

    assert await _bodies() == ["please fix the flaky login test", "a prompt typed into Claude Code"]


async def test_slack_edits_replace_the_text_and_deletes_remove_it(registry_db: None) -> None:
    message: JsonObject = {
        "type": "message",
        "channel": "CPUBLIC01",
        "user": "U1",
        "ts": "1760000000.000100",
        "text": "first draft",
    }
    await _slack(message)
    await _slack({**message, "type": "app_mention"})
    await _slack({**message, "ts": "1760000000.000200", "bot_id": "B1", "text": "a bot"})
    await _slack({**message, "ts": "1760000000.000300", "subtype": "channel_join"})
    await _slack(
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "CPUBLIC01",
            "message": {**message, "text": "second draft"},
        }
    )
    assert await _bodies() == ["second draft"]

    await _slack(
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "CPUBLIC01",
            "deleted_ts": "1760000000.000100",
        }
    )
    assert await _bodies() == []


async def test_readers_see_only_text_they_may_read(registry_db: None, no_embedder: None) -> None:
    await SlackChannel.save_all(_PUBLIC_CHANNEL, _PRIVATE_CHANNEL)
    for channel, user, ts in (
        ("CPUBLIC01", "U1", "1760000000.000100"),
        ("CPRIVATE1", "U1", "1760000000.000200"),
        ("CPRIVATE1", "U2", "1760000000.000300"),
    ):
        await _slack(
            {
                "type": "message",
                "channel": channel,
                "user": user,
                "ts": ts,
                "text": f"migration plan from {channel} by {user}",
            }
        )
    await _request(await _thread("private"), _envelope("migration plan in a private thread"))

    async def found(reader: Reader) -> set[str]:
        return {hit.text for hit in await search.search("migration plan", reader)}

    assert await found(Reader()) == {"migration plan from CPUBLIC01 by U1"}
    assert await found(Reader(login="someone", slack_user_id="U1")) == {
        "migration plan from CPUBLIC01 by U1",
        "migration plan from CPRIVATE1 by U1",
    }
    assert await found(Reader(login="owner")) == {
        "migration plan from CPUBLIC01 by U1",
        "migration plan in a private thread",
    }


async def test_search_matches_by_meaning_once_embedded(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = _TopicEmbedder(AsyncOpenAI(api_key="test"))
    monkeypatch.setattr(Embedder, "configured", classmethod(lambda cls: embedder))
    thread_id = await _thread()
    await _request(thread_id, _envelope("we deploy every friday afternoon"))
    await _request(thread_id, _envelope("lunch is at noon"))

    assert await search.search("when does it go out to production", Reader()) == []
    assert await embed_pending() == 2
    [hit] = (await search.search("when do we ship", Reader()))[:1]
    assert hit.text == "we deploy every friday afternoon"
