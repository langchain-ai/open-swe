"""Time from a Slack message that triggers a run to the bot's first message back."""

import logging

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel

from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_NAMESPACE = "slack_first_response"
# Bounds how long an unanswered trigger can claim a later, unrelated bot post.
_TTL_MINUTES = 60


class PendingSlackFirstResponse(BaseModel):
    trigger_ts: str
    run_id: str
    agent_thread_id: str
    code_channel: bool
    dm_session: bool
    reply_thread_timestamps: list[str]

    async def register(self, client: LangGraphClient, channel_id: str) -> None:
        value = self.model_dump()
        try:
            for thread_ts in self.reply_thread_timestamps:
                await client.store.put_item(
                    (_NAMESPACE, channel_id), thread_ts, value, ttl=_TTL_MINUTES
                )
        except Exception:
            logger.warning(
                "Failed to start Slack first response timer",
                extra={"slack_channel": channel_id, "agent_thread_id": self.agent_thread_id},
                exc_info=True,
            )

    @classmethod
    async def resolve(cls, channel_id: str, thread_ts: str | None, reply_ts: str) -> None:
        if not thread_ts:
            return
        client = langgraph_client()
        namespace = (_NAMESPACE, channel_id)
        try:
            item = await client.store.get_item(namespace, thread_ts)
            if not item:
                return
            pending = cls.model_validate(item["value"])
            latency_ms = round((float(reply_ts) - float(pending.trigger_ts)) * 1000)
        except NotFoundError:
            return
        except Exception:
            logger.warning(
                "Failed to read Slack first response timer",
                extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
                exc_info=True,
            )
            return
        if latency_ms < 0:
            return
        try:
            for key in pending.reply_thread_timestamps:
                await client.store.delete_item(namespace, key=key)
        except Exception:
            logger.warning(
                "Failed to clear Slack first response timer",
                extra={"slack_channel": channel_id, "agent_thread_id": pending.agent_thread_id},
                exc_info=True,
            )
        logger.info(
            "Slack first response",
            extra={
                "latency_ms": latency_ms,
                "slack_channel": channel_id,
                "agent_thread_id": pending.agent_thread_id,
                "run_id": pending.run_id,
                "code_channel": pending.code_channel,
                "dm_session": pending.dm_session,
            },
        )
