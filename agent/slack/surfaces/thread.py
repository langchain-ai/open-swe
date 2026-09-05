"""A session that lives in one Slack thread."""

from agent.slack.surfaces.base import SlackSurface


class SlackThreadSurface(SlackSurface):
    """A session that lives in one Slack thread, alongside whatever else is in the channel."""

    kind = "slack_thread"

    def __init__(self, channel_id: str, thread_ts: str) -> None:
        self.channel_id = channel_id
        self.thread_ts = thread_ts

    def reply_target(self) -> str:
        return self.thread_ts
