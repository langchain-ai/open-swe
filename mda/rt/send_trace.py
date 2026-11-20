"""A Slack message's LangSmith trace, from its posting to the end of the run it starts.

The Slack channel's handler opens it once it decides a message starts a turn, timed
from when Slack says the message was posted. ``delivery`` spans Slack, Trigger Server
and MDA handing the message over; ``channel`` spans the handler, with a span for each
step it takes, and the backend's answers nest under the step that asked
(``rt.intake.ask_backend``). The run the handler starts names the trace in its
metadata (``runtime_api.slack.SEND_TRACE_ID_KEY``), and the run's completion webhook
adds the run's stages and closes it. A message that starts no run closes it here.

Tracing never raises into the handler.
"""

import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from langsmith.run_helpers import trace
from langsmith.run_trees import RunTree
from langsmith.utils import tracing_is_enabled
from runtime_api.slack import (
    SEND_DOTTED_ORDER_KEY,
    SEND_PROJECT_KEY,
    SEND_TRACE_ID_KEY,
    SlackTurnRun,
)

logger = logging.getLogger(__name__)

# The root metadata trace access policies read, as the backend stamped it on the run.
_TRACE_ACCESS_KEYS = ("owner_ls_user_id", "visibility")


class SlackSendTrace:
    """One Slack message's trace while the channel handles it; inert when it isn't traced."""

    def __init__(self, root: RunTree | None = None, channel: RunTree | None = None) -> None:
        self.root = root
        # The handler's span, open until the handler starts a run or none.
        self.channel = channel

    @staticmethod
    def posted_at(message_ts: str) -> datetime | None:
        """When Slack says a message was posted, from its timestamp (epoch seconds)."""
        try:
            return datetime.fromtimestamp(float(message_ts), UTC)
        except ValueError:
            return None

    @classmethod
    def open(
        cls,
        *,
        thread_id: str,
        channel_id: str,
        message_ts: str,
        text: str,
        received_at: datetime,
        facts: tuple[datetime, datetime] | None = None,
    ) -> SlackSendTrace:
        """Start the trace of a message that starts a turn, which the handler received at
        ``received_at`` and asked the backend about during ``facts``."""
        posted = cls.posted_at(message_ts)
        if posted is None or not tracing_is_enabled():
            return cls()
        try:
            trace_id = uuid.uuid7()
            # Slack's clock may run ahead of this host's.
            started = min(posted, received_at)
            root = RunTree(
                name="slack.send",
                id=trace_id,
                trace_id=trace_id,
                run_type="chain",
                start_time=started,
                inputs={"text": text},
                extra={
                    "metadata": {
                        "thread_id": thread_id,
                        "slack_channel_id": channel_id,
                        "slack_message_ts": message_ts,
                        # Matches the origin the backend records on the run it starts.
                        "origin": "slack",
                    }
                },
            )
            root.create_child("delivery", start_time=started, end_time=received_at).post()
            channel = root.create_child("channel", start_time=received_at)
            channel.post()
            if facts is not None:
                channel.create_child(
                    "slack_message_facts", start_time=facts[0], end_time=facts[1]
                ).post()
        except Exception:
            logger.warning("Could not open a Slack message's trace", exc_info=True)
            return cls()
        return cls(root, channel)

    @contextmanager
    def step(self, name: str) -> Generator[None]:
        """A span of one step the handler takes; a backend call made in it nests under it."""
        if self.root is None or self.channel is None:
            yield
            return
        with trace(name, parent=self.channel, client=self.root.client):
            yield

    def started(self, run: SlackTurnRun, *, thread_id: str) -> None:
        """Hand the trace to the run the handler starts: the run's metadata names it, for
        its completion to close, and the root names the run's thread and invocation."""
        if self.root is None:
            return
        run.metadata[SEND_TRACE_ID_KEY] = str(self.root.id)
        run.metadata[SEND_DOTTED_ORDER_KEY] = self.root.dotted_order
        # This deployment's project, which the backend's own may not be.
        if self.root.session_name:
            run.metadata[SEND_PROJECT_KEY] = self.root.session_name
        invocation = run.metadata.get("invocation_id")
        self.root.add_metadata(
            {
                "thread_id": thread_id,
                **({"invocation_id": invocation} if isinstance(invocation, str) else {}),
                # The root carries the message, so it takes the run's access before its only post.
                **{
                    key: value
                    for key in _TRACE_ACCESS_KEYS
                    if isinstance(value := run.metadata.get(key), str)
                },
            }
        )
        self._end_channel()
        self._post_root()

    def no_run(self, outcome: str, *, error: str | None = None) -> None:
        """Close the trace of a message that starts no run, saying why."""
        if self.root is None:
            return
        self._end_channel(error)
        try:
            # No run says whose thread the message is in, so its trace is hidden as private.
            self.root.add_metadata({"visibility": "private"})
            self.root.end(outputs={"outcome": outcome}, error=error)
        except Exception:
            logger.warning("Could not close a Slack message's trace", exc_info=True)
        self._post_root()

    def _end_channel(self, error: str | None = None) -> None:
        channel, self.channel = self.channel, None
        if channel is None:
            return
        try:
            channel.end(error=error)
            channel.patch()
        except Exception:
            logger.warning("Could not close a Slack message's channel span", exc_info=True)

    def _post_root(self) -> None:
        """Post the root once: any update to a posted run also ends it."""
        root, self.root = self.root, None
        if root is None:
            return
        try:
            root.post()
        except Exception:
            logger.warning("Could not post a Slack message's trace", exc_info=True)
