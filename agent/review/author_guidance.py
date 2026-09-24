"""The human input behind an Open SWE pull request: the opening request and every follow-up.

Open SWE already records every human turn: a thread's checkpointed ``messages``
hold the text and the ``<input-message>`` envelope that says whether a person
typed it or the platform generated it, and ``pull_request_thread`` maps a PR
back to the agent threads that produced it. :class:`SteeringHistory` reads those
turns so they can be put in front of the review scout.

Reading them through ``threads.get_state`` rather than the transcript tables is
deliberate: the checkpoint is the stable record. Compaction never costs a turn,
because deepagents keeps ``state["messages"]`` intact and tracks the summary
beside it.

The review scout reads these messages alongside the code and summarises what
people asked for in one piece of prose, stored on the walkthrough.
"""

import html
import logging
import re
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict

from agent.github.pull_requests import PullRequest
from agent.input_messages import input_message_text, message_sender_id
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

MAX_FOLLOW_UPS = 40
MAX_MESSAGE_CHARS = 4_000
# Keeps a message's own text from closing the block it is quoted in.
_CLOSING_MESSAGE_TAG_RE = re.compile(r"</\s*(author_messages|message)\s*>", re.IGNORECASE)


class _StateMessage(BaseModel):
    """One entry of a thread's checkpointed ``messages``, as the SDK returns it."""

    model_config = ConfigDict(extra="ignore")

    type: str = ""
    # Absent on a message written straight into state rather than through a run.
    id: str | None = None
    content: str | list[dict[str, Any]] = ""


class HumanTurn(BaseModel):
    """One message a person actually typed into a thread behind this PR."""

    thread_id: str
    message_id: str
    author: str
    text: str


class SteeringHistory(BaseModel):
    """The opening ask, and every human turn after it."""

    request: HumanTurn
    follow_ups: list[HumanTurn]

    @classmethod
    async def load(cls, owner: str, repo: str, pr_number: int) -> Self | None:
        """This PR's human turns, or ``None`` when Open SWE did not write it."""
        pull_request = await PullRequest.get(owner, repo, pr_number)
        if pull_request is None:
            return None
        thread_ids = await pull_request.linked_threads()
        if not thread_ids:
            return None
        turns = [turn for thread_id in thread_ids for turn in await cls._human_turns(thread_id)]
        if not turns:
            return None
        return cls(request=turns[0], follow_ups=turns[1:][-MAX_FOLLOW_UPS:])

    @property
    def turns(self) -> list[HumanTurn]:
        return [self.request, *self.follow_ups]

    @staticmethod
    async def _human_turns(thread_id: str) -> list[HumanTurn]:
        """Every message a person typed into one thread, oldest first.

        A ``human`` message covers platform-generated wake-ups too — they ride
        the same channel — so the envelope decides: only a ``kind="human"``
        ``<input-message>`` was typed by someone. The sender id names them
        (``github:<login>``), and the checkpoint keeps the messages in the order
        they arrived, which is the order the steering happened.
        """
        try:
            state = await langgraph_client().threads.get_state(thread_id)
        except Exception:
            logger.warning(
                "Could not read thread state for steering history",
                exc_info=True,
                extra={"steering_thread_id": thread_id},
            )
            return []
        values = state.get("values") if isinstance(state, Mapping) else None
        raw = values.get("messages") if isinstance(values, Mapping) else None
        turns: list[HumanTurn] = []
        for entry in raw if isinstance(raw, list) else []:
            message = _StateMessage.model_validate(entry)
            if message.type != "human":
                continue
            sender = message_sender_id(message.content, kind="human")
            if sender is None:
                continue
            body = (input_message_text(message.content) or "").strip()
            if not body:
                continue
            turns.append(
                HumanTurn(
                    thread_id=thread_id,
                    message_id=message.id or "",
                    author=sender.split(":", 1)[-1] or "unknown",
                    text=body[:MAX_MESSAGE_CHARS],
                )
            )
        return turns

    def messages_block(self) -> str:
        """Every human message as a ``<message author="..." turn="...">`` entry, oldest first."""
        return "\n".join(
            f'<message author="{html.escape(turn.author)}" '
            f'turn="{"opening request" if turn is self.request else "follow-up"}">\n'
            f"{_CLOSING_MESSAGE_TAG_RE.sub(lambda m: f'</{m.group(1)}_>', turn.text)}\n"
            "</message>"
            for turn in self.turns
        )
