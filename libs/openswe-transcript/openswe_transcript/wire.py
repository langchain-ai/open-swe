"""The contract between a remote runtime and the backend's transcript.

A runtime that calls the model itself streams the reply's fragments to the
backend as ``FragmentBatch`` bodies, and names the message they used on the
reply (``mark_streamed``) so the backend completes that message rather than
starting another.
"""

from typing import Annotated, Final
from uuid import UUID

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

PATH: Final = "/remote-runtime/transcript"
"""Where the backend takes a remote run's fragments, with the run's token as the bearer."""

STREAM_MESSAGE_ID_KEY: Final = "open_swe_transcript_message_id"
"""``response_metadata`` key naming the transcript message a reply was streamed under."""

MAX_BATCH_FRAGMENTS: Final = 64

type _Id = Annotated[str, Field(min_length=1, max_length=256)]


class Fragment(BaseModel):
    """A flushed piece of one AI message: text and reasoning to append to what it holds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Names the append, so delivering a fragment twice appends it once.
    fragment_id: UUID
    message_id: _Id
    namespace: list[_Id] = Field(default_factory=list, max_length=16)
    text: str | None = None
    reasoning: str | None = None


class FragmentBatch(BaseModel):
    """Fragments one run streamed, in the order it streamed them."""

    model_config = ConfigDict(extra="forbid")

    run_id: _Id
    fragments: list[Fragment] = Field(min_length=1, max_length=MAX_BATCH_FRAGMENTS)


def streamed_message_id(message: BaseMessage) -> str | None:
    """The transcript message ``message`` was streamed under, if a writer named one."""
    value = message.response_metadata.get(STREAM_MESSAGE_ID_KEY)
    return value if isinstance(value, str) and value else None


def mark_streamed(message: BaseMessage, message_id: str) -> None:
    """Name the transcript message ``message`` was streamed under, on the message itself.

    It travels with the message wherever the message goes, so any process that
    later sees the message, or a tool call it issued, keys it the same way.
    """
    message.response_metadata[STREAM_MESSAGE_ID_KEY] = message_id
