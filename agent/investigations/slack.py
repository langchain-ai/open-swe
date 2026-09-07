"""Narrow Slack operations used by the investigation coordinator and publisher."""

from typing import Any

import httpx

from agent.config import ENV
from agent.investigations.models import InvestigationMessage, InvestigationPolicy


class SlackError(RuntimeError):
    def __init__(self, code: str, retry_after: float = 60):
        super().__init__(code)
        self.retry_after = retry_after


async def request(method: str, *, write: bool = False, **params: Any) -> dict[str, Any]:
    token = ENV.SLACK_BOT_TOKEN.get()
    if not token:
        raise SlackError("missing_slack_bot_token")
    async with httpx.AsyncClient(timeout=20) as client:
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://slack.com/api/{method}"
        if write:
            response = await client.post(url, headers=headers, json=params)
        else:
            response = await client.get(url, headers=headers, params=params)
    if response.status_code == 429:
        try:
            delay = float(response.headers.get("Retry-After", "60"))
        except ValueError:
            delay = 60
        raise SlackError("rate_limited", delay)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise SlackError(str(data.get("error", "slack_error")))
    if method == "auth.test" and "x-oauth-scopes" in response.headers:
        data["granted_scopes"] = [
            scope.strip() for scope in response.headers["x-oauth-scopes"].split(",")
        ]
    return data


async def channel_info(channel_id: str) -> dict[str, Any]:
    return (await request("conversations.info", channel=channel_id))["channel"]


def channel_allowed(
    channel: dict[str, Any], policy: InvestigationPolicy, *, for_read: bool = False
) -> bool:
    public_internal = (
        channel.get("is_channel") is True
        and channel.get("is_private") is False
        and channel.get("is_im") is not True
        and channel.get("is_mpim") is not True
        and channel.get("is_ext_shared") is False
        and channel.get("is_pending_ext_shared") is False
    )
    if not public_internal:
        return False
    if for_read:
        return channel.get("is_member") is True
    return (
        str(channel.get("name", "")).startswith(policy.channel_prefix)
        and channel.get("id") not in policy.excluded_channel_ids
        and channel.get("is_archived") is not True
    )


async def join(channel_id: str) -> None:
    await request("conversations.join", write=True, channel=channel_id)


def message(channel_id: str, data: dict[str, Any]) -> InvestigationMessage:
    from agent.investigations.evidence_tools import redact

    ts = str(data.get("ts") or data.get("deleted_ts") or "")
    text = str(data.get("text") or "")
    for attachment in data.get("attachments") or []:
        if isinstance(attachment, dict):
            text += "\n" + str(attachment.get("text") or attachment.get("fallback") or "")
    for block in data.get("blocks") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), dict):
            value = block["text"].get("text", "")
            if value and value not in text:
                text += "\n" + str(value)
    return InvestigationMessage(
        id=ts,
        ts=ts,
        thread_ts=str(data.get("thread_ts") or ts),
        user=str(data.get("user") or ""),
        bot_id=str(data.get("bot_id") or ""),
        app_id=str(data.get("app_id") or (data.get("bot_profile") or {}).get("app_id") or ""),
        event_type=str((data.get("metadata") or {}).get("event_type") or ""),
        text=redact(text, 8000),
        edited_at=str((data.get("edited") or {}).get("ts") or ts),
        source_url=f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}",
    )


async def history(
    channel_id: str, policy: InvestigationPolicy
) -> tuple[list[InvestigationMessage], list[str]]:
    messages: dict[str, InvestigationMessage] = {}
    gaps: list[str] = []
    cursor = ""
    parents: list[str] = []
    for _ in range(3):
        data = await request("conversations.history", channel=channel_id, limit=100, cursor=cursor)
        for item in data.get("messages", []):
            msg = message(channel_id, item)
            if msg.ts:
                messages[msg.ts] = msg
            if item.get("reply_count"):
                parents.append(msg.ts)
        cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    if cursor:
        gaps.append("Channel history is limited to the most recent 300 messages.")
    for parent in parents[:10]:
        try:
            replies = await request(
                "conversations.replies", channel=channel_id, ts=parent, limit=100
            )
            for item in replies.get("messages", []):
                msg = message(channel_id, item)
                if msg.ts:
                    messages[msg.ts] = msg
            if replies.get("has_more") or (replies.get("response_metadata") or {}).get(
                "next_cursor"
            ):
                gaps.append(f"Replies to {parent} are incomplete.")
        except SlackError, httpx.HTTPError:
            gaps.append(f"Replies to {parent} could not be read.")
    if len(parents) > 10:
        gaps.append("Only the ten most recent reply threads were read.")
    return sorted(messages.values(), key=lambda item: float(item.ts or 0))[-500:], gaps


async def publish(channel_id: str, text: str, thread_ts: str | None, publication_id: str) -> str:
    params: dict[str, Any] = {
        "channel": channel_id,
        "text": text[:12000],
        "unfurl_links": False,
        "unfurl_media": False,
        "metadata": {
            "event_type": "investigate_report",
            "event_payload": {"publication_id": publication_id},
        },
    }
    if thread_ts:
        params["thread_ts"] = thread_ts
    return str((await request("chat.postMessage", write=True, **params))["ts"])
