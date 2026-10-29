"""A `/btw` question finished in the asker's DM, then answered once where it was asked.

``slack_btw_ask_in_dm`` DMs the asker and binds that DM thread to the question's own
private agent thread, which records it here (``slack_btw_dm``). Replies there continue
the question; ``slack_btw_answer`` posts the answer where it was asked, as any `/btw`
answer is posted, and marks the DM answered: from then on it is the asker's own
conversation.
"""

from typing import Self

from pydantic import BaseModel, ValidationError

from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.store import THREADS

METADATA_KEY = "slack_btw_dm"


class ByTheWayDm(BaseModel):
    """The DM thread a `/btw` question continues in."""

    channel_id: str
    thread_ts: str
    question: str = ""
    # Where the question was asked.
    permalink: str = ""
    answered: bool = False

    @classmethod
    async def of(cls, thread_id: str) -> tuple[Self, SourceContext] | None:
        """The DM ``thread_id`` continues in, and where its question was asked."""
        thread = await index.get(thread_id)
        raw = thread.metadata.get(METADATA_KEY) if thread is not None else None
        if thread is None or not isinstance(raw, dict):
            return None
        try:
            return cls.model_validate(raw), SourceContext.from_metadata(thread.metadata)
        except ValidationError:
            return None

    @classmethod
    async def at(cls, thread_id: str, channel_id: str, thread_ts: str, user_id: str) -> Self | None:
        """The DM a message there continues, when it is the asker's own."""
        found = await cls.of(thread_id)
        if found is None:
            return None
        dm, asked_in = found
        asker = asked_in.slack_thread.triggering_user_id if asked_in.slack_thread else ""
        here = (dm.channel_id, dm.thread_ts) == (channel_id, thread_ts)
        return dm if here and user_id and asker == user_id else None

    async def save(self, thread_id: str) -> None:
        await THREADS.update(thread_id, {METADATA_KEY: self.model_dump(mode="json")})
