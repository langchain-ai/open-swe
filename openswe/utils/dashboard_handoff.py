from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from openswe.input_messages import FieldValue, FieldValues, InputMessageContext

DASHBOARD_HANDOFF_SENDER_ID = "system:dashboard-handoff"
DASHBOARD_HANDOFF_BODY = (
    "This follow-up was sent from Web. The conversation has moved to Web, so answer in "
    "the dashboard stream with a normal assistant message. Do not call slack_reply "
    "unless a later Slack message explicitly moves the conversation back to Slack."
)
# Stamped by the proxy, never trusted from a client: another agent thread sent
# this on its person's behalf, so it must not move the conversation to Web.
RELAYED_FROM_KEY = "relayed_from_thread_id"
# Where a dashboard message carries its replies: beside the content, so they
# survive both the transcript and the SDK stream paths.
REPLIES_KEY = "replies"
# Stamped by the backend on something a person did in the app rather than typed: it
# counts as their intervention, but like a relay it must not move the conversation to Web.
ACTION_KEY = "action"

_CONTEXT_LIMIT = 4_000
_SELECTION_LIMIT = 20_000


class PassageReply(BaseModel):
    """A passage the sender selected and replied to.

    ``before``, ``selection`` and ``after`` are contiguous in the source, so the agent
    can tell exactly which part was selected and read it in place.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["conversation", "terminal", "file"]
    file: str | None = Field(default=None, max_length=1_000)
    lines: str | None = Field(default=None, max_length=40, pattern=r"^\d+(-\d+)?$")
    before: str = Field(default="", max_length=_CONTEXT_LIMIT)
    selection: str = Field(min_length=1, max_length=_SELECTION_LIMIT)
    after: str = Field(default="", max_length=_CONTEXT_LIMIT)
    comment: str = Field(default="", max_length=_SELECTION_LIMIT)

    def fields(self) -> FieldValues:
        """The reply as the model reads it, source first and the comment last."""
        return {
            "source": self.source,
            "file": self.file,
            "lines": self.lines,
            "before": self.before,
            "selection": self.selection,
            "after": self.after,
            "comment": self.comment,
        }


_PASSAGE_REPLIES = TypeAdapter(list[PassageReply])


def passage_replies(raw: object) -> list[PassageReply]:
    """The replies a dashboard message carries; raises ``ValidationError`` on a bad one."""
    return [] if raw is None else _PASSAGE_REPLIES.validate_python(raw)


def dashboard_input_context(
    sender_id: str,
    relayed_from: str | None,
    replies: Sequence[PassageReply] = (),
    action: str | None = None,
) -> InputMessageContext:
    """The envelope of a dashboard send: typed on Web, relayed by another agent thread, or an action taken in the app."""
    data: dict[str, FieldValue | FieldValues | list[FieldValues]] = {}
    if replies:
        data["reply"] = [reply.fields() for reply in replies]
    context: InputMessageContext = {"sender_id": sender_id, "surface": "web", "kind": "human"}
    if relayed_from is not None:
        context["surface"] = "automation"
        data["relayed_from_thread"] = relayed_from
    if action is not None:
        context["surface"] = "automation"
        data["action"] = action
    if data:
        context["data"] = data
    return context
