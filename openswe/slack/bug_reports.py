"""Bug reports posted where a workspace routes them, each started as a thread in its kitchen.

A workspace can point a bound channel, such as a team's help channel, at one of
its kitchen channels. Each new top-level message a person posts there is
classified, and every bug report becomes a breakout thread in the kitchen that
acts for its author, as if they had posted it there, linked from the original.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from openswe import event_claims
from openswe.input_messages import SystemIdentity
from openswe.prompts import prompt
from openswe.slack import webhook
from openswe.slack.blocks import block_payload
from openswe.slack.breakout_destination import resolve_breakout_destination
from openswe.slack.breakout_links import (
    BreakoutOrigin,
    breakout_root_blocks,
    notify_breakout,
    source_thread_line,
)
from openswe.slack.client import (
    append_slack_web_link_footer,
    get_slack_permalink,
    post_slack_top_level_message_with_ts,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.payloads import SlackChannelContext, SlackEventEnvelope
from openswe.slack.records import bind_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.thread_ids import slack_thread_id
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.jev import select_jev_choice
from openswe.webhooks import common
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

BUG_REPORT_TRIAGE: SystemIdentity = {
    "id": "system:slack-bug-reports",
    "display_name": "Bug report triage",
    "platform": "slack",
}
_CLAIM_SCOPE = "slack_bug_report"
_CLAIM_TTL = timedelta(days=1)
# A burst of reports in one channel must not flood its kitchen with runs.
_THREADS_PER_HOUR = 10
_RATE_WINDOW = timedelta(hours=1)
_CLASSIFIED_CHARS = 4_000
_UNLINKED_REPORTER = (
    "Open SWE didn't start on this because whoever reported it hasn't connected an Open SWE "
    "account. Reply in this thread to have Open SWE look into it for you."
)


@dataclass(frozen=True)
class SlackBugReport:
    """A person's top-level message in a channel that routes its bug reports to a kitchen."""

    channel_id: str
    ts: str
    user_id: str
    text: str
    kitchen_id: str
    event_id: str
    team_id: str
    bot_user_id: str

    @classmethod
    async def posted(
        cls, envelope: SlackEventEnvelope, channel: SlackChannelContext, bot_user_id: str
    ) -> SlackBugReport | None:
        """The message this event posts, when its channel routes bug reports and it may be one."""
        event = envelope.event
        if (
            event is None
            or not event.top_level_post
            or event.is_from_bot
            or not channel.allows_operations
        ):
            return None
        user_id = event.resolve_user_id()
        text = event.shown_text
        # A message that tags Open SWE is answered where it was asked.
        if (
            not user_id
            or user_id == bot_user_id
            or not text.strip()
            or (bot_user_id and f"<@{bot_user_id}>" in text)
        ):
            return None
        channel_id = event.resolve_channel_id()
        try:
            kitchen_id = await WORKSPACES.bug_report_kitchen(channel_id)
        except Exception:
            logger.exception(
                "Bug report route lookup failed", extra={"slack_channel_id": channel_id}
            )
            return None
        if kitchen_id is None:
            return None
        return cls(
            channel_id=channel_id,
            ts=event.ts,
            user_id=user_id,
            text=text,
            kitchen_id=kitchen_id,
            event_id=envelope.event_id,
            team_id=envelope.team_id or event.team,
            bot_user_id=bot_user_id,
        )

    @property
    def _log_context(self) -> dict[str, str]:
        return {
            "slack_channel_id": self.channel_id,
            "slack_message_ts": self.ts,
            "kitchen_channel_id": self.kitchen_id,
        }

    @property
    def _claim_group(self) -> str:
        return f"{self.channel_id}:"

    @property
    def _claim_key(self) -> str:
        return f"{self._claim_group}{self.ts}"

    async def triage(self) -> None:
        """Start a thread in the kitchen when this message reports a bug."""
        try:
            if await self._reports_bug() and await self._claim():
                await self._start_thread()
        except Exception:
            logger.exception("Slack bug report triage failed", extra=self._log_context)

    async def _reports_bug(self) -> bool:
        choice = await select_jev_choice(
            self.text[:_CLASSIFIED_CHARS],
            question="bug_report",
            instructions=prompt("slack/bug-report-instructions"),
            criteria={
                "bug_report": prompt("slack/bug-report-yes"),
                "not_bug_report": prompt("slack/bug-report-no"),
            },
        )
        logger.info(
            "Classified a Slack message for bug report triage",
            extra={**self._log_context, "bug_report_choice": choice},
        )
        return choice == "bug_report"

    async def _claim(self) -> bool:
        outcome = await event_claims.claim_within_limit(
            _CLAIM_SCOPE,
            self._claim_key,
            ttl=_CLAIM_TTL,
            group=self._claim_group,
            limit=_THREADS_PER_HOUR,
            within=_RATE_WINDOW,
        )
        if outcome == "limited":
            logger.warning("Bug report triage hit its hourly limit", extra=self._log_context)
        return outcome == "claimed"

    async def _start_thread(self) -> None:
        destination = await resolve_breakout_destination(self.channel_id, self.kitchen_id)
        if error := await destination.channel_error(self.channel_id):
            logger.warning(
                "Bug report kitchen unavailable",
                extra={**self._log_context, "breakout_error": error},
            )
            await event_claims.release(_CLAIM_SCOPE, self._claim_key)
            return
        label = f"Bug report from <#{self.channel_id}>"
        suffix = "".join(
            f" · {part}"
            for part in (await source_thread_line(self.channel_id, self.ts), f"<@{self.user_id}>")
            if part
        )
        opened = await self._open_kitchen_thread(f"{label}{suffix}")
        if opened is None:
            return
        root_ts, thread_id = opened
        unlinked = await webhook.account_link_needed(self.user_id) is not None
        link = await get_slack_permalink(self.kitchen_id, root_ts)
        await notify_breakout(
            self.channel_id,
            self.ts,
            f"Open SWE {'shared' if unlinked else 'is looking into'} this bug report in "
            f"{f'<{link}|a thread>' if link else 'a thread'} in <#{self.kitchen_id}>.",
        )
        if unlinked:
            await notify_breakout(self.kitchen_id, root_ts, _UNLINKED_REPORTER)
            return
        repo = await common.get_slack_repo_config(
            self.kitchen_id, root_ts, slack_user_id=self.user_id, thread_id=thread_id
        )
        await webhook.process_slack_mention(
            SlackRequest(
                channel_id=self.kitchen_id,
                thread_ts=root_ts,
                event_ts=root_ts,
                original_message_ts=root_ts,
                event_id=self.event_id,
                user_id=self.user_id,
                text=prompt(
                    "slack/bug-report",
                    channel_id=self.channel_id,
                    message_ts=self.ts,
                    kitchen_id=self.kitchen_id,
                    reporter=self.user_id,
                ),
                bot_user_id=self.bot_user_id,
                trigger_system=BUG_REPORT_TRIAGE,
                thread_id=thread_id,
                team_id=self.team_id,
                context_channel_id=self.channel_id,
                context_thread_ts=self.ts,
                breakout_root_label=label,
                breakout_root_suffix=suffix,
                treat_all_messages_as_mentions=True,
                kitchen_channel=True,
            ),
            repo,
            inherited_workspace=destination.workspace,
        )

    async def _open_kitchen_thread(self, root_text: str) -> tuple[str, str] | None:
        """Post the thread's root in the kitchen and bind its agent thread: ``(ts, thread id)``."""
        try:
            root_ts = await post_slack_top_level_message_with_ts(
                self.kitchen_id,
                root_text,
                blocks=block_payload(breakout_root_blocks(root_text, None)),
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Bug report kitchen thread could not be posted",
                extra={**self._log_context, "slack_error": exc.code},
            )
            await event_claims.release(_CLAIM_SCOPE, self._claim_key)
            return None
        # Recorded first: whatever stops the run from starting below, the first reply
        # in the kitchen thread still starts from the report.
        await BreakoutOrigin(channel_id=self.channel_id, thread_ts=self.ts).save_for(
            self.kitchen_id, root_ts
        )
        thread_id = await bind_slack_thread_id(
            self.kitchen_id, root_ts, slack_thread_id(self.kitchen_id, root_ts)
        )
        if web_url := dashboard_thread_url(thread_id):
            try:
                await update_slack_message(
                    self.kitchen_id,
                    root_ts,
                    append_slack_web_link_footer(root_text, web_url),
                    blocks=block_payload(breakout_root_blocks(root_text, web_url)),
                    unfurl_links=False,
                    unfurl_media=False,
                )
            except SlackRequestError as exc:
                logger.warning(
                    "Bug report kitchen thread web link not added",
                    extra={**self._log_context, "slack_error": exc.code},
                )
        return root_ts, thread_id
