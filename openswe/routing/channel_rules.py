"""Join new channels a channel name rule matches, and start the rule's kickoff run there."""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final

from pydantic import JsonValue

from openswe.config import ENV
from openswe.dispatch import create_durable_run
from openswe.input_messages import SystemIdentity, system_input, system_introduction
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.routing.store import INTEGRATIONS
from openswe.slack.channels import SlackChannel
from openswe.slack.client import fetch_slack_thread_messages, parse_slack_ts
from openswe.slack.http import SlackClient
from openswe.slack.payloads import SlackChannelPayload
from openswe.slack.records import resolve_slack_thread_id
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

# Kickoff threads keep the source incident channels gave them: plugins and stored
# threads read it.
KICKOFF_SOURCE = "incidents_agent"
# Named as incident channels queued them, so jobs queued before rules replaced them still run.
JOIN_JOB: Final = "incident_channel_join"
KICKOFF_JOB: Final = "incident_channel_investigate"
# A new channel's alert or description usually lands just after it is created.
KICKOFF_DELAY_SECONDS = 60
_ENROLLING_EVENTS = frozenset({"channel_created", "channel_rename"})
_CHANNEL_RULES_SYSTEM: SystemIdentity = {
    "id": "system:channel-rules",
    "display_name": "Channel rules",
    "platform": "open-swe",
}
_KICKOFF_NOTICE = (
    "Robin joined this channel and is getting started. Updates will appear in this thread."
)


class RuleChannel:
    """A Slack channel a channel name rule matches, which Robin joins once.

    Joining and the kickoff run are jobs keyed by the channel, so overlapping creation
    and rename events queue one join, and a failed step retries.
    """

    def __init__(self, channel_id: str, name: str) -> None:
        self.channel_id = channel_id
        self.name = name

    @classmethod
    def from_event(cls, event: Mapping[str, object]) -> RuleChannel | None:
        """The channel a creation or rename event names."""
        channel = event.get("channel")
        if str(event.get("type") or "") not in _ENROLLING_EVENTS or not isinstance(channel, dict):
            return None
        channel_id = str(channel.get("id") or "")
        name = str(channel.get("name") or "").lower()
        return cls(channel_id, name) if channel_id and name else None

    @classmethod
    def of(cls, job: Job) -> RuleChannel:
        return cls(str(job.payload["channel_id"]), str(job.payload["name"]))

    @property
    def payload(self) -> dict[str, JsonValue]:
        return {"channel_id": self.channel_id, "name": self.name}

    async def queue_join(self) -> None:
        """Queue joining the channel, unless a join of it is already queued."""
        await queue.schedule(
            JOIN_JOB, self.payload, key=f"{JOIN_JOB}:{self.channel_id}", replace=False
        )

    @classmethod
    async def join(cls, job: Job) -> None:
        """Join the channel and, when its rule has a kickoff, post the notice and queue the run.

        Every attempt checks a rule still matches the channel, but only the first skips
        one Robin is already in: a retry follows its own join, which failed before the
        kickoff was queued, and reuses the notice that join posted.
        """
        channel = cls.of(job)
        info = await SlackChannel.fetch(channel.channel_id, use_cache=False)
        if not info:
            raise RuntimeError("Slack did not describe the channel")
        details = SlackChannelPayload.of(info)
        rule = await INTEGRATIONS.channel_rule(details.name)
        if rule is None or not details.is_public or details.is_archived is True:
            logger.info("Skipping a channel no rule matches", extra=channel.log_extra)
            return
        first = job.attempts == 0 and job.lapsed_lease is None
        if first and details.is_member is True:
            return
        async with SlackClient.bot() as slack:
            await slack.conversations_join(channel=channel.channel_id)
            if not rule.kickoff.strip():
                return
            thread_ts = "" if first else await channel.posted_notice(job.created_at)
            if not thread_ts:
                posted = await slack.chat_postMessage(
                    channel=channel.channel_id,
                    text=_KICKOFF_NOTICE,
                    unfurl_links=False,
                    unfurl_media=False,
                )
                thread_ts = str(posted.get("ts") or "")
        if not thread_ts:
            raise RuntimeError("Slack did not return the kickoff message timestamp")
        await queue.schedule(
            KICKOFF_JOB,
            {**channel.payload, "thread_ts": thread_ts, "kickoff": rule.kickoff},
            key=f"{KICKOFF_JOB}:{channel.channel_id}",
            replace=False,
        )

    async def posted_notice(self, since: datetime) -> str:
        """The timestamp of the kickoff notice Robin posted here since ``since``, or ""."""
        bot_user = ENV.SLACK_BOT_USER_ID.get()
        for message in reversed(
            await fetch_slack_thread_messages(self.channel_id, "0", complete=True)
        ):
            ts = str(message.get("ts") or "")
            if (
                message.get("text") == _KICKOFF_NOTICE
                and message.get("user") == bot_user
                and parse_slack_ts(ts) >= since.timestamp()
            ):
                return ts
        return ""

    @classmethod
    async def kickoff(cls, job: Job) -> None:
        """Start the rule's kickoff run in the notice's thread, once.

        A retry, or a job reclaimed from a worker that stopped, first checks the thread
        for a run, so a run the server started before an error reached us is not
        started again.
        """
        channel = cls.of(job)
        thread_ts = str(job.payload["thread_ts"])
        kickoff = job.payload.get("kickoff")
        if not isinstance(kickoff, str):
            rule = await INTEGRATIONS.channel_rule(channel.name)
            kickoff = rule.kickoff if rule else ""
        if not kickoff.strip():
            return
        thread_id = await resolve_slack_thread_id(channel.channel_id, thread_ts)
        slack_thread = SlackThreadRef(channel_id=channel.channel_id, thread_ts=thread_ts)
        if not await upsert_agent_thread_metadata(
            thread_id,
            source=KICKOFF_SOURCE,
            title=f"#{channel.name}",
            source_context=SourceContext(slack_thread=slack_thread),
            visibility="public",
            owner_type="system",
            # The channel's name as the run began, so readers need not parse the title.
            extra_metadata={"incident_channel": {"id": channel.channel_id, "name": channel.name}},
        ):
            raise RuntimeError("could not persist kickoff thread metadata")
        if (job.attempts or job.lapsed_lease is not None) and (
            thread := await THREADS.get(thread_id)
        ) is not None:
            if await RUNTIMES.runs_on(thread.runtime).latest_run(thread_id) is not None:
                return
        await create_durable_run(
            thread_id,
            "agent",
            input={
                "messages": [
                    system_introduction(_CHANNEL_RULES_SYSTEM),
                    system_input(
                        prompt(
                            "slack/channel-kickoff",
                            channel_name=channel.name,
                            kickoff=kickoff,
                        ),
                        {
                            "sender_id": _CHANNEL_RULES_SYSTEM["id"],
                            "channel_id": f"slack:{channel.channel_id}",
                            "surface": "slack",
                            "kind": "system",
                        },
                    ),
                ]
            },
            source=KICKOFF_SOURCE,
            thread_title=None,
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "source": KICKOFF_SOURCE,
                    "slack_thread": {"channel_id": channel.channel_id, "thread_ts": thread_ts},
                }
            },
            metadata={"source": KICKOFF_SOURCE},
            multitask_strategy="enqueue",
            after_seconds=KICKOFF_DELAY_SECONDS,
        )

    @property
    def log_extra(self) -> dict[str, object]:
        return {"rule_channel": {"channel_id": self.channel_id, "name": self.name}}


async def handle_slack_event(event: Mapping[str, object]) -> dict[str, str] | None:
    """Queue joining a created or renamed channel a rule matches; None lets the regular path continue.

    A redelivered or overlapping event queues nothing more: the join is keyed by the
    channel, and a join that already ran finds Robin in the channel.
    """
    channel = RuleChannel.from_event(event)
    if channel is None or await INTEGRATIONS.channel_rule(channel.name) is None:
        return None
    await channel.queue_join()
    return {"status": "accepted"}
