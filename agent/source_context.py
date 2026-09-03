"""The shape of ``source_context`` — where a thread came from.

``source_context`` rides along in LangGraph thread metadata and in the baby-sit
watch record, and is read in a dozen modules to answer "which Slack thread /
Linear issue / GitHub issue started this run?".

Two rules make the models here different from the store records:

**Unknown keys survive.** Writers add keys this module has never heard of
(``breakout_from``, per-integration extras), and several call sites read a
context, enrich it, and write it back. ``extra="allow"`` plus
:meth:`SourceContext.dump` (which excludes unset fields) makes that round-trip
byte-identical — a plain ``model_dump`` would inject defaults for every field the
original omitted.

**Parsing never raises.** Thread metadata is written by webhooks and by older
deployments, and is not validated on write. A context that will not parse yields
an empty one, because losing the Slack thread of a run is better than failing it.
"""

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


class SlackThreadRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel_id: str = ""
    thread_ts: str = ""
    reply_thread_ts: str = ""
    trace_message_ts: str = ""
    triggering_user_id: str = ""
    triggering_user_name: str = ""
    triggering_user_email: str = ""
    triggering_user_timezone: str = ""
    triggering_event_ts: str = ""
    permalink: str = ""
    channel_context: dict[str, Any] | None = None

    @property
    def location(self) -> tuple[str, str] | None:
        """``(channel_id, thread_ts)`` when both are present."""
        if self.channel_id and self.thread_ts:
            return self.channel_id, self.thread_ts
        return None

    def is_at(self, channel_id: str, thread_ts: str) -> bool:
        return self.channel_id == channel_id and self.thread_ts == thread_ts

    def dump(self) -> dict[str, Any]:
        """The JSON value to store, preserving exactly the keys that were set."""
        return self.model_dump(mode="json", exclude_unset=True)


class LinearIssueRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    identifier: str = ""
    url: str = ""


class GitHubIssueRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    number: int | None = None
    url: str = ""


class SourceContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    slack_thread: SlackThreadRef | None = None
    linear_issue: LinearIssueRef | None = None
    github_issue: GitHubIssueRef | None = None
    pr_number: int | None = None

    @classmethod
    def parse(cls, raw: Any) -> "SourceContext":
        if isinstance(raw, SourceContext):
            return raw
        if not isinstance(raw, Mapping):
            return cls()
        try:
            return cls.model_validate(dict(raw))
        except ValidationError:
            logger.warning("Unparseable source_context, ignoring", exc_info=True)
            return cls()

    @classmethod
    def from_metadata(cls, metadata: Any) -> "SourceContext":
        if not isinstance(metadata, Mapping):
            return cls()
        return cls.parse(metadata.get("source_context"))

    def dump(self) -> dict[str, Any]:
        """The JSON value to store, preserving exactly the keys that were set."""
        return self.model_dump(mode="json", exclude_unset=True)

    @property
    def slack_location(self) -> tuple[str, str] | None:
        return self.slack_thread.location if self.slack_thread else None

    @property
    def is_empty(self) -> bool:
        return not self.dump()

    def get(self, key: str) -> Any:
        """Value for ``key``, whether it is a declared field or an extra."""
        if key in type(self).model_fields:
            return getattr(self, key)
        return (self.model_extra or {}).get(key)
