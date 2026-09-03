"""A session that owns a whole Slack code channel."""

from typing import Any

from agent.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    DEFAULT_CODE_CHANNEL_COMMANDS,
    rename_session,
    repo_context_bar_items,
    set_agent_resource,
    set_commands,
    set_context_bar,
    set_session_status,
    set_view,
)
from agent.slack.surfaces.base import SlackSurface
from agent.utils.dashboard_links import dashboard_thread_url

CODE_CHANNEL_PROMPT_SECTION = (
    "## Slack Code Channel\n"
    "The whole channel is one session with you. Treat messages as addressed to you unless "
    "clearly aimed at someone else.\n"
    "Everything you say reaches the channel as you say it — your replies are the transcript, "
    'so just answer, and never repeat yourself to "send" a message. Say what you are doing '
    "and what you found, in Slack mrkdwn (*bold*, _italic_, <url|link text>, bullets with "
    '"• "), and keep it short enough to read in a channel.\n'
    "Use `slack_reply_to_message` only to answer under one specific earlier message, "
    "`ask_user_choice` when you are blocked on a decision with known answers, and "
    "`manage_code_channel` for session status, title, context, runtime commands, "
    "HTML/diff/Block Kit/canvas views, and archival."
)


class SlackChannelSurface(SlackSurface):
    """A session that owns a whole Slack code channel, chrome included."""

    kind = "slack_channel"
    reports_activity = True
    has_chrome = True
    projects_transcript = True

    def __init__(self, channel_id: str, reply_thread_ts: str = "") -> None:
        self.channel_id = channel_id
        self.reply_thread_ts = reply_thread_ts

    def prompt_section(self) -> str:
        return CODE_CHANNEL_PROMPT_SECTION

    def reply_target(self) -> str:
        return self.reply_thread_ts or CODE_CHANNEL_SESSION_TS

    def viewer_link_thread_id(self, thread_id: str) -> None:
        # The channel is the session, so a per-thread web link would point users
        # away from the surface they are already reading.
        return None

    async def begin_turn(self) -> None:
        await set_session_status(self.channel_id, "processing")

    async def end_turn(self) -> None:
        await set_session_status(self.channel_id, "active")

    async def start_session(self, *, repo: dict[str, Any] | None, thread_id: str) -> None:
        await self.sync_context(repo=repo, thread_id=thread_id)
        await set_commands(self.channel_id, DEFAULT_CODE_CHANNEL_COMMANDS)

    async def sync_context(
        self,
        *,
        repo: dict[str, Any] | None,
        thread_id: str,
        branch: str = "",
        pr_url: str = "",
    ) -> None:
        items = repo_context_bar_items(
            repo,
            branch=branch,
            pr_url=pr_url,
            dashboard_url=dashboard_thread_url(thread_id) or "",
        )
        if items:
            await set_context_bar(self.channel_id, items)

    async def set_resource(self, resource: dict[str, Any]) -> None:
        await set_agent_resource(self.channel_id, resource)

    async def publish_diff(self, content: str, *, base_branch: str, head_branch: str) -> None:
        await set_view(
            self.channel_id,
            "diff",
            content=content,
            base_branch=base_branch,
            head_branch=head_branch,
        )

    async def set_title(self, title: str) -> None:
        await rename_session(self.channel_id, title)
