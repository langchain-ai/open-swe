"""Where a run was asked from, decided from the run's config, for its memory."""

from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import MemoryScope

from openswe.run_config import RunConfig


class RunMemoryScope:
    """The ``MemoryScope`` of a run. Anything that can't be confirmed counts the strict way."""

    @staticmethod
    def for_run(config: RunnableConfig) -> MemoryScope:
        cfg = RunConfig.from_config(config)
        slack = cfg.slack_thread
        if slack is None:
            public_repo = cfg.source == "github" and cfg.repo_private is not True
            return MemoryScope(thread="external" if public_repo else "other")
        channel = slack.channel_context
        # Slack only says a channel isn't shared outside the company, not a DM, as the
        # operations gate reads it.
        if channel is None or not (slack.is_dm or channel.allows_operations):
            return MemoryScope(thread="external")
        conversation = slack.is_dm or channel.is_im is not False or channel.is_mpim is not False
        public = not conversation and channel.is_private is False
        return MemoryScope(thread="public" if public else "other")
