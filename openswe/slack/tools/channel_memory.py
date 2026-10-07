"""Propose shared memory changes in the current Slack channel."""

from difflib import unified_diff

from openswe.run_config import RunConfig
from openswe.slack.blocks import block_payload, code_block, section
from openswe.slack.channel_memory import PROPOSAL_NAMESPACE, channel_member
from openswe.slack.channels import SlackChannel
from openswe.slack.client import fetch_slack_message_by_ts, post_slack_thread_reply_with_ts
from openswe.store import put_value


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
    current, revision = await SlackChannel.memory_file(slack.channel_id)
    text = memory.strip()
    patch = "\n".join(
        unified_diff(
            current.splitlines(),
            text.splitlines(),
            fromfile="channel-memory.md",
            tofile="channel-memory.md",
            lineterm="",
        )
    )
    if not patch:
        return {"success": False, "error": "The proposed memory is unchanged"}
    if len(code_block(patch)) > 2900:
        return {
            "success": False,
            "error": "Patch is too large for one review card; propose a smaller edit",
        }
    message_text = (
        f"<@{slack.triggering_user_id}> proposes this patch to channel-memory.md:\n"
        f"```\n{patch}\n```\n\n"
        "A different human channel member must react 👍 to this message to apply this exact patch."
    )
    blocks = block_payload(
        [
            section(f"*Channel memory patch*\nProposed by <@{slack.triggering_user_id}>"),
            section(code_block(patch)),
            section("React 👍 to second this exact patch. The proposer cannot second it."),
        ]
    )
    timestamp, error = await post_slack_thread_reply_with_ts(
        slack.channel_id,
        slack.reply_thread_ts or slack.thread_ts,
        message_text,
        blocks=blocks,
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
            "base_revision": revision,
            "patch": patch,
            "message_blocks": message.get("blocks", []),
            "message_text": message["text"],
            "status": "pending",
        },
    )
    return {"success": True, "status": "pending", "message_ts": timestamp}
