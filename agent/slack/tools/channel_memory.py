"""Propose shared memory changes in the current Slack channel."""

from agent.run_config import RunConfig
from agent.slack.channel_memory import MEMORY_NAMESPACE, PROPOSAL_NAMESPACE, channel_member
from agent.slack.client import fetch_slack_message_by_ts, post_slack_thread_reply_with_ts
from agent.store import get_value, put_value


async def propose_channel_memory(memory: str) -> dict[str, object]:
    cfg = RunConfig.from_runtime()
    slack = cfg.slack_thread
    if not slack or not slack.channel_id or not slack.triggering_user_id:
        return {"success": False, "error": "A Slack channel and triggering member are required"}
    context = slack.channel_context
    if not context or not context.allows_operations or context.is_im or context.is_mpim:
        return {
            "success": False,
            "error": "Channel memory is only available in internal Slack channels",
        }
    if len(memory) > 20_000:
        return {"success": False, "error": "Memory must be at most 20,000 characters"}
    if not await channel_member(slack.channel_id, slack.triggering_user_id):
        return {"success": False, "error": "Only human channel members can propose memory"}
    current = await get_value(MEMORY_NAMESPACE, slack.channel_id) or {}
    text = memory.strip()
    message_text = (
        f"<@{slack.triggering_user_id}> proposes replacing this channel's memory with:\n"
        f"{text or '(empty — clear channel memory)'}\n\n"
        "A different human channel member must react 👍 to this message to apply this exact proposal."
    )
    timestamp, error = await post_slack_thread_reply_with_ts(
        slack.channel_id,
        slack.reply_thread_ts or slack.thread_ts,
        message_text,
        unfurl_links=False,
        unfurl_media=False,
    )
    if not timestamp:
        return {"success": False, "error": error or "Could not post memory proposal"}
    message = await fetch_slack_message_by_ts(slack.channel_id, timestamp)
    if not message or not isinstance(message.get("text"), str):
        return {"success": False, "error": "Could not verify the posted proposal; propose again"}
    await put_value(
        PROPOSAL_NAMESPACE,
        f"{slack.channel_id}:{timestamp}",
        {
            "memory": text,
            "proposer": slack.triggering_user_id,
            "base_revision": current.get("revision", 0),
            "message_text": message["text"],
            "status": "pending",
        },
    )
    return {"success": True, "status": "pending", "message_ts": timestamp}
