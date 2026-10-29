"""A `/btw` question finished in the asker's DM: asked there, answered once where it was asked.

A `/btw` run posts one public answer and ends. When it can't answer without asking
the asker something, it never posts a partial answer: it DMs the asker instead
(``slack_btw_ask_in_dm``), and the DM's Slack thread is bound to the same private
agent thread, so each reply there continues the question. Once the answer is
complete, ``slack_btw_answer`` posts it in the thread it was asked in, the one
reservation every `/btw` answer takes, and detaches the DM: later turns there are
the asker's private conversation and can no longer post to that thread.

The thread's metadata records the DM (``slack_btw_dm``); its ``source_context``
keeps where the question was asked, which no later turn repoints.
"""

import logging
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, JsonValue, ValidationError

from openswe.slack.records import lookup_slack_thread_id
from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.store import THREADS

logger = logging.getLogger(__name__)

METADATA_KEY = "slack_btw_dm"


class ByTheWayDm(BaseModel):
    """The DM thread a `/btw` question continues in, and whether its answer was posted."""

    channel_id: str
    thread_ts: str
    question: str = ""
    # The Slack thread the question was asked in, for the asker and the model.
    permalink: str = ""
    answered: bool = False

    @classmethod
    def of(cls, metadata: Mapping[str, JsonValue]) -> Self | None:
        raw = metadata.get(METADATA_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Unparseable /btw DM record, ignoring", exc_info=True)
            return None

    def is_at(self, channel_id: str, thread_ts: str) -> bool:
        return (self.channel_id, self.thread_ts) == (channel_id, thread_ts)

    async def save(self, thread_id: str) -> None:
        await THREADS.update(thread_id, {METADATA_KEY: self.model_dump(mode="json")})


async def thread_metadata(thread_id: str) -> dict[str, JsonValue]:
    """The thread's metadata as the thread index keeps it; empty for an unknown thread.

    A `/btw` thread is always indexed, so a DM thread new to the index is none.
    """
    thread = await index.get(thread_id)
    return {} if thread is None else thread.metadata


class ByTheWayFollowUp(BaseModel):
    """A turn in the DM a `/btw` question continues in."""

    dm: ByTheWayDm
    asked_in: SourceContext

    @classmethod
    async def at(cls, thread_id: str, channel_id: str, thread_ts: str, user_id: str) -> Self | None:
        """The `/btw` question ``thread_id`` continues in this DM thread, if any.

        Only the asker's own messages count: the DM is theirs, and so is the agent thread.
        """
        metadata = await thread_metadata(thread_id)
        dm = ByTheWayDm.of(metadata)
        asked_in = SourceContext.from_metadata(metadata)
        asker = asked_in.slack_thread.triggering_user_id if asked_in.slack_thread else ""
        if dm is None or not dm.is_at(channel_id, thread_ts) or not user_id or asker != user_id:
            return None
        return cls(dm=dm, asked_in=asked_in)


async def continues_in_dm(channel_id: str, thread_ts: str) -> bool:
    """Whether this DM thread continues a `/btw` question still waiting for its answer.

    A concierge DM routes every message to one conversation; a reply in this thread
    stays in it instead, so it reaches the question.
    """
    thread_id = await lookup_slack_thread_id(channel_id, thread_ts)
    if thread_id is None:
        return False
    dm = ByTheWayDm.of(await thread_metadata(thread_id))
    return dm is not None and not dm.answered and dm.is_at(channel_id, thread_ts)
