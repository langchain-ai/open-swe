"""`@Open SWE /breakout`, handled without waking the current thread's agent."""

import logging
import re

from agent.slack import webhook as service
from agent.slack.breakout_links import mark_broken_out, source_thread_line
from agent.slack.client import (
    get_active_slack_thread,
    post_slack_ephemeral_reply,
    post_slack_top_level_message_with_ts,
    strip_bot_mention,
)
from agent.slack.move import move_slack_thread
from agent.slack.request import SlackRequest
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.webhooks import common

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"/breakout(?:\s+(?P<instruction>.*))?", re.DOTALL | re.IGNORECASE)
_TITLE_MAX_CHARS = 160


def parse_breakout_command(text: str, bot_user_id: str) -> str | None:
    """The text after `/breakout` ("" when bare), or None when the message is not the command."""
    clean = strip_bot_mention(text, bot_user_id, bot_username=common.SLACK_BOT_USERNAME)
    match = _COMMAND_RE.fullmatch(clean)
    if match is None:
        return None
    return (match.group("instruction") or "").strip()


def _title(instruction: str) -> str:
    first_line = instruction.splitlines()[0].strip() if instruction.strip() else ""
    if len(first_line) <= _TITLE_MAX_CHARS:
        return first_line
    return f"{first_line[: _TITLE_MAX_CHARS - 1].rstrip()}…"


def _command_ts(request: SlackRequest) -> str:
    return request.original_message_ts or request.event_ts


async def _root_text(request: SlackRequest, heading: str) -> str:
    parts = (
        heading,
        await source_thread_line(request.channel_id, _command_ts(request) or request.thread_ts),
        f"<@{request.user_id}>" if request.user_id else "",
    )
    return " · ".join(part for part in parts if part)


async def _mark_done(request: SlackRequest) -> None:
    await mark_broken_out(request.channel_id, _command_ts(request))


async def _tell_sender(request: SlackRequest, text: str) -> None:
    if request.user_id:
        await post_slack_ephemeral_reply(request.channel_id, request.user_id, text)


async def _move(request: SlackRequest) -> None:
    thread_id = request.thread_id
    if not thread_id or not await common.thread_exists(thread_id):
        await _tell_sender(request, "There is nothing here to break out yet.")
        return
    client = langgraph_client()
    metadata = thread_metadata(await client.threads.get(thread_id))
    if common.thread_is_private(metadata):
        await _tell_sender(request, "Private threads cannot be broken out.")
        return
    active = await get_active_slack_thread(client, thread_id)
    if not active or (active.get("channel_id"), active.get("thread_ts")) != (
        request.channel_id,
        request.thread_ts,
    ):
        await _tell_sender(request, "This thread already moved to another Slack thread.")
        return

    title = str(metadata.get("title") or "").strip() or "Untitled"
    heading = f"*Breakout thread:* {_title(title)}"
    result = await move_slack_thread(
        client,
        thread_id,
        active,
        request.channel_id,
        await _root_text(request, heading),
    )
    new_ts = result.get("thread_ts")
    if not result.get("success") or not isinstance(new_ts, str):
        logger.warning(
            "Slack breakout move failed",
            extra={"agent_thread_id": thread_id, "move_error": result.get("error")},
        )
        await _tell_sender(request, "Could not move this thread; try again.")
        return
    await _mark_done(request)


async def _start(
    request: SlackRequest, instruction: str, repo: common.SlackRepoResolution | None
) -> None:
    heading = f"*Breakout thread:* {_title(instruction)}"
    new_ts, slack_error = await post_slack_top_level_message_with_ts(
        request.channel_id,
        await _root_text(request, heading),
        unfurl_links=False,
        unfurl_media=False,
    )
    if not new_ts:
        logger.warning("Slack breakout root post failed", extra={"slack_error": slack_error})
        await _tell_sender(request, "Could not start a breakout thread; try again.")
        return
    await _mark_done(request)
    thread_id = await common.resolve_slack_thread_id(langgraph_client(), request.channel_id, new_ts)
    await service.process_slack_mention(
        request.model_copy(
            update={
                "thread_ts": new_ts,
                "thread_id": thread_id,
                "text": instruction,
                "context_thread_ts": request.thread_ts,
            }
        ),
        repo,
    )


async def process_slack_breakout(
    request: SlackRequest, instruction: str, repo: common.SlackRepoResolution | None
) -> None:
    """Move this thread when bare; otherwise start a new one seeded with this thread's transcript."""
    try:
        if instruction:
            await _start(request, instruction, repo)
        else:
            await _move(request)
    except Exception:
        logger.exception("Slack breakout failed", extra={"agent_thread_id": request.thread_id})
        await _tell_sender(request, "Could not break out this thread; try again.")
