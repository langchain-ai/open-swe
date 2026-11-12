"""Join new incident channels and start an investigation in each."""

import logging
from datetime import datetime
from typing import Any, Final

from fastapi import HTTPException
from pydantic import JsonValue

from openswe.config import ENV
from openswe.dashboard.options import normalize_model_choice
from openswe.dispatch import create_durable_run
from openswe.incidents import service
from openswe.incidents.models import IncidentPolicy
from openswe.input_messages import SystemIdentity, system_input, system_introduction
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.slack.channels import SlackChannel
from openswe.slack.client import fetch_slack_thread_messages, parse_slack_ts
from openswe.slack.http import SlackClient
from openswe.slack.records import resolve_slack_thread_id
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

INCIDENT_SOURCE = "incidents_agent"
JOIN_JOB: Final = "incident_channel_join"
INVESTIGATE_JOB: Final = "incident_channel_investigate"
# A new channel's alert or description usually lands just after it is created.
KICKOFF_DELAY_SECONDS = 60
_ENROLLING_EVENTS = frozenset({"channel_created", "channel_rename"})
_INCIDENTS_SYSTEM: SystemIdentity = {
    "id": "system:incidents",
    "display_name": "Incidents",
    "platform": "open-swe",
}
_KICKOFF_NOTICE = "Robin joined to investigate. Findings will appear in this thread."


class IncidentChannel:
    """A Slack channel named like an incident, which Robin joins once to investigate.

    Joining and investigating are jobs keyed by the channel, so overlapping creation
    and rename events queue one kickoff, and a failed step retries.
    """

    def __init__(self, channel_id: str, name: str) -> None:
        self.channel_id = channel_id
        self.name = name

    @classmethod
    def from_event(cls, payload: dict[str, Any], policy: IncidentPolicy) -> IncidentChannel | None:
        """The channel a creation or rename event names, if the policy wants Robin there."""
        event = payload.get("event") or {}
        channel = event.get("channel")
        if str(event.get("type") or "") not in _ENROLLING_EVENTS or not isinstance(channel, dict):
            return None
        channel_id = str(channel.get("id") or "")
        name = str(channel.get("name") or "")
        if (
            not policy.enabled
            or not channel_id
            or not name.startswith(policy.channel_prefix)
            or channel_id in policy.excluded_channel_ids
        ):
            return None
        return cls(channel_id, name)

    @classmethod
    def of(cls, job: Job) -> IncidentChannel:
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
        """Join the channel, post the kickoff notice, and queue the investigation under it.

        Every attempt checks the channel is still eligible, but only the first skips one
        Robin is already in: a retry follows its own join, which failed before the
        investigation was queued, and reuses the notice that join posted.
        """
        channel = cls.of(job)
        policy = await service.get_policy()
        if not policy.enabled or channel.channel_id in policy.excluded_channel_ids:
            return
        info = await SlackChannel.fetch(channel.channel_id, use_cache=False)
        if not info:
            raise RuntimeError("Slack did not describe the incident channel")
        if not service.channel_allowed(info, policy):
            logger.info("Skipping ineligible incident channel", extra=channel.log_extra)
            return
        first = job.attempts == 0 and job.lapsed_lease is None
        if first and info.get("is_member") is True:
            return
        async with SlackClient.bot() as slack:
            await slack.conversations_join(channel=channel.channel_id)
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
            INVESTIGATE_JOB,
            {**channel.payload, "thread_ts": thread_ts},
            key=f"{INVESTIGATE_JOB}:{channel.channel_id}",
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
    async def investigate(cls, job: Job) -> None:
        """Start the investigation run in the kickoff notice's thread, once.

        A retry, or a job reclaimed from a worker that stopped, first checks the thread
        for a run, so a run the server started before an error reached us is not
        started again.
        """
        channel = cls.of(job)
        thread_ts = str(job.payload["thread_ts"])
        policy = await service.get_policy()
        thread_id = await resolve_slack_thread_id(channel.channel_id, thread_ts)
        slack_thread = SlackThreadRef(channel_id=channel.channel_id, thread_ts=thread_ts)
        if not await upsert_agent_thread_metadata(
            thread_id,
            source=INCIDENT_SOURCE,
            title=f"#{channel.name}",
            source_context=SourceContext(slack_thread=slack_thread),
            visibility="public",
            owner_type="system",
        ):
            raise RuntimeError("could not persist incident thread metadata")
        if (job.attempts or job.lapsed_lease is not None) and (
            thread := await THREADS.get(thread_id)
        ) is not None:
            if await RUNTIMES.runs_on(thread.runtime).latest_run(thread_id) is not None:
                return
        configurable: dict[str, Any] = {
            "thread_id": thread_id,
            "source": INCIDENT_SOURCE,
            "slack_thread": {"channel_id": channel.channel_id, "thread_ts": thread_ts},
        }
        if policy.model:
            model, effort = normalize_model_choice(policy.model, None)
            configurable["agent_model_id"] = model
            configurable["agent_effort"] = effort
        await create_durable_run(
            thread_id,
            "agent",
            input={
                "messages": [
                    system_introduction(_INCIDENTS_SYSTEM),
                    system_input(
                        prompt("incidents/kickoff", channel_name=channel.name),
                        {
                            "sender_id": _INCIDENTS_SYSTEM["id"],
                            "channel_id": f"slack:{channel.channel_id}",
                            "surface": "slack",
                            "kind": "system",
                        },
                    ),
                ]
            },
            source=INCIDENT_SOURCE,
            thread_title=None,
            config={"configurable": configurable},
            metadata={"source": INCIDENT_SOURCE},
            multitask_strategy="enqueue",
            after_seconds=KICKOFF_DELAY_SECONDS,
        )

    @property
    def log_extra(self) -> dict[str, object]:
        return {"incident_channel": {"channel_id": self.channel_id, "name": self.name}}


async def handle_slack_event(payload: dict[str, Any]) -> dict[str, str] | None:
    """Queue joining a newly created or renamed incident channel; None lets the regular path continue.

    A redelivered or overlapping event queues nothing more: the join is keyed by the
    channel, and a join that already ran finds Robin in the channel.
    """
    policy = await service.get_policy()
    channel = IncidentChannel.from_event(payload, policy)
    if channel is None:
        return None
    if (
        payload.get("team_id") != policy.workspace_id
        or payload.get("api_app_id") != policy.slack_app_id
    ):
        raise HTTPException(401, "Slack workspace or app does not match the incident policy")
    await channel.queue_join()
    return {"status": "accepted"}
