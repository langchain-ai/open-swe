"""Channel-scoped memory proposals seconded by Slack members."""

from collections.abc import Mapping

from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    fetch_slack_message_by_ts,
    get_slack_user_info,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
)
from openswe.slack.http import SlackClient
from openswe.store import get_value, put_value
from openswe.utils.thread_ops import langgraph_client

PROPOSAL_NAMESPACE = ["slack_channel_memory_proposals"]


async def channel_member(channel_id: str, user_id: str) -> bool:
    cursor = ""
    async with SlackClient.bot() as client:
        while True:
            response = await client.conversations_members(
                channel=channel_id, cursor=cursor, limit=200
            )
            members = response.get("members")
            if not isinstance(members, list):
                raise ValueError("Invalid Slack membership response")
            if user_id in members:
                info = await get_slack_user_info(user_id)
                return bool(info and not info.get("is_bot") and not info.get("deleted"))
            metadata = response.get("response_metadata")
            next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
            if not isinstance(next_cursor, str) or (next_cursor and next_cursor == cursor):
                raise ValueError("Invalid Slack membership cursor")
            if not next_cursor:
                return False
            cursor = next_cursor


async def approve_channel_memory(event: Mapping[str, object]) -> bool:
    if event.get("reaction") not in {"+1", "thumbsup"}:
        return False
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "message":
        return False
    channel, timestamp, user = item.get("channel"), item.get("ts"), event.get("user")
    if not all(isinstance(value, str) and value for value in (channel, timestamp, user)):
        return False
    assert isinstance(channel, str) and isinstance(timestamp, str) and isinstance(user, str)
    key = f"{channel}:{timestamp}"
    proposal = await get_value(PROPOSAL_NAMESPACE, key)
    if not proposal:
        return False
    async with slack_thread_mutation_lock(
        langgraph_client(), channel, "0.000000", purpose="memory"
    ):
        proposal = await get_value(PROPOSAL_NAMESPACE, key)
        if not proposal or proposal.get("status") != "pending":
            return True
        if user == proposal.get("proposer") or not await channel_member(channel, user):
            return True
        proposer = proposal.get("proposer")
        if not isinstance(proposer, str) or not await channel_member(channel, proposer):
            return True
        message = await fetch_slack_message_by_ts(channel, timestamp)
        if (
            not message
            or message.get("text") != proposal.get("message_text")
            or message.get("blocks", []) != proposal.get("message_blocks", [])
        ):
            return True
        memory, revision = proposal.get("memory"), proposal.get("base_revision")
        if not isinstance(memory, str) or not isinstance(revision, int):
            raise ValueError("Invalid channel memory proposal")
        patch = proposal.get("patch")
        if not isinstance(patch, str):
            raise ValueError("Invalid channel memory patch")
        if not await SlackChannel.patch_memory(
            channel,
            memory,
            revision,
            patch=patch,
            proposed_by=proposer,
            approved_by=user,
            proposal_ts=timestamp,
        ):
            proposal["status"] = "stale"
            await put_value(PROPOSAL_NAMESPACE, key, proposal)
            await post_slack_thread_reply_with_ts(
                channel,
                timestamp,
                "Channel memory changed since this proposal. Please propose it again against the current memory.",
            )
            return True
        proposal["seconded_by"] = user
        proposal["status"] = "applied"
        await put_value(PROPOSAL_NAMESPACE, key, proposal)
    await post_slack_thread_reply_with_ts(
        channel, timestamp, f"Channel memory updated, seconded by <@{user}>."
    )
    return True
