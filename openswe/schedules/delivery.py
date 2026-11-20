"""Authorize automation delivery to server-recorded Slack destinations."""

import logging
from dataclasses import dataclass
from typing import Final, Self

from fastapi import HTTPException

from openswe.dashboard.oauth import enforce_github_login_gate
from openswe.run_config import AutomationSlackReply, RunConfig
from openswe.schedules.store import (
    authorized_schedule_run,
    record_run_error,
    schedule_thread_metadata,
)
from openswe.slack.blocks import MESSAGE_MAX_BLOCKS, block_payload, context, escape
from openswe.slack.channels import SlackChannel
from openswe.slack.client import convert_mentions_to_slack_format
from openswe.slack.dm import post_dm
from openswe.slack.markdown import markdown_blocks, markdown_to_mrkdwn
from openswe.slack.records import lookup_slack_thread_id
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

_SLACK_TEXT_MAX_CHARS: Final = 40_000


class DeliveryRefused(Exception):
    """Why this automation run cannot deliver, worded for the model."""


class AutomationThreadDelivery(AutomationSlackReply):
    """Authorize replies only to the automation event's saved Slack destination."""

    @classmethod
    async def for_run(cls, cfg: RunConfig) -> Self:
        route = cfg.automation_slack_reply
        metadata = await schedule_thread_metadata(cfg)
        if (
            route is None
            or metadata is None
            or metadata.get("schedule_id") != cfg.schedule_id
            or metadata.get("automation_slack_reply")
            != route.model_dump(mode="json", exclude_unset=True)
            or await authorized_schedule_run(cfg) is None
        ):
            raise DeliveryRefused("This run has no authorized Slack automation reply destination.")
        channel = await SlackChannel.load(route.channel_id, use_cache=False)
        if channel is None or channel.id != route.channel_id or not channel.can_listen:
            raise DeliveryRefused(
                "The triggering Slack channel is no longer verified and readable."
            )
        owner = await lookup_slack_thread_id(route.channel_id, route.thread_ts)
        if owner is not None and owner != cfg.thread_id:
            raise DeliveryRefused("Another Robin conversation owns the triggering Slack thread.")
        return cls.model_validate(route.model_dump())


@dataclass(frozen=True)
class CreatorDelivery:
    """An automation result delivered only to its creator through their DM."""

    schedule_id: str
    schedule_name: str
    thread_id: str
    creator_login: str
    slack_user_id: str

    @classmethod
    async def for_run(cls, cfg: RunConfig) -> Self:
        """The creator of the automation this run belongs to, rechecked against the login gate."""
        record = await authorized_schedule_run(cfg)
        if record is None or not cfg.thread_id:
            raise DeliveryRefused(
                "Only an automation run on its own thread can deliver to the automation's creator."
            )
        schedule_id = str(record["id"])
        creator = record.get("created_by")
        login = creator.strip() if isinstance(creator, str) else ""
        log_context = {
            "schedule_id": schedule_id,
            "agent_thread_id": cfg.thread_id,
            "creator_login": login or None,
        }
        if not login:
            logger.warning("Automation has no creator to deliver to", extra=log_context)
            raise DeliveryRefused("This automation has no recorded creator to deliver to.")
        try:
            await enforce_github_login_gate(login)
        except HTTPException as exc:
            logger.warning(
                "Refused automation delivery at the creator login gate",
                extra={**log_context, "status_code": exc.status_code},
            )
            if exc.status_code == 403:
                error = f"The automation's creator {login} is no longer authorized to use Robin."
            else:
                error = f"Could not verify whether the automation's creator {login} is authorized."
                if exc.status_code == 503 and isinstance(exc.detail, str):
                    error += f" {exc.detail}"
            await record_run_error(record, error)
            raise DeliveryRefused(f"{error} Nothing was delivered.") from exc
        user = await User.for_login("github", login)
        slack_user_id = user.slack_user_id if user is not None else ""
        if not slack_user_id:
            logger.warning("Automation creator has no linked Slack account", extra=log_context)
            error = f"The automation's creator {login} has no linked Slack account to deliver to."
            await record_run_error(record, error)
            raise DeliveryRefused(f"{error} Nothing was delivered.")
        name = record.get("name")
        return cls(
            schedule_id=schedule_id,
            schedule_name=name if isinstance(name, str) and name else "Automation",
            thread_id=cfg.thread_id,
            creator_login=login,
            slack_user_id=slack_user_id,
        )

    def _footer(self) -> str:
        parts = [f"From the automation *{escape(self.schedule_name)}*"]
        if url := dashboard_thread_url(self.thread_id):
            parts.append(f"<{url}|Web thread>")
        return " · ".join(parts)

    async def send(self, message: str) -> tuple[str, str]:
        """DM ``message`` (Markdown) to the creator; the ``(channel_id, message_ts)`` it landed at.

        Raises ``SlackRequestError`` when Slack refuses it.
        """
        text = convert_mentions_to_slack_format(message.strip())
        footer = self._footer()
        fallback = f"{markdown_to_mrkdwn(text)}\n\n{footer}"
        if len(fallback) > _SLACK_TEXT_MAX_CHARS:
            raise DeliveryRefused(
                f"The message is too long for Slack; keep it under {_SLACK_TEXT_MAX_CHARS} "
                "characters."
            )
        blocks = markdown_blocks(text)
        # Past Slack's block limit the plain text, which carries the footer too, is the message.
        if blocks is not None and len(blocks) < MESSAGE_MAX_BLOCKS:
            blocks.append(context(footer))
        else:
            blocks = None
        location = await post_dm(
            self.slack_user_id, fallback, blocks=block_payload(blocks) if blocks else None
        )
        logger.info(
            "Delivered an automation result to its creator",
            extra={
                "schedule_id": self.schedule_id,
                "agent_thread_id": self.thread_id,
                "creator_login": self.creator_login,
                "slack_channel": location[0],
                "slack_message_ts": location[1],
            },
        )
        return location
