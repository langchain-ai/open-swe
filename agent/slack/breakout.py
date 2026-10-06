"""`@Open SWE /breakout`, handled without waking the current thread's agent."""

import logging
import re
from dataclasses import dataclass

from agent.slack import webhook as service
from agent.slack.blocks import block_payload, section
from agent.slack.breakout_destination import resolve_breakout_destination
from agent.slack.breakout_links import mark_broken_out, source_thread_line
from agent.slack.cards import origin_footer
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    append_slack_web_link_footer,
    get_active_slack_thread,
    post_slack_ephemeral_message,
    post_slack_top_level_message_with_ts,
    update_slack_message,
)
from agent.slack.http import SlackRequestError
from agent.slack.move import move_slack_thread
from agent.slack.request import SlackRequest
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.webhooks import common
from agent.workspaces.store import parse_workspace_tag

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"/breakout(?:\s+(?P<instruction>.*))?", re.DOTALL | re.IGNORECASE)
_CHANNEL_MENTION_RE = re.compile(r"<#(?P<channel_id>[CG][A-Z0-9]+)(?:\|[^>]*)?>")
_TITLE_MAX_CHARS = 160
_CHANNEL_REJECTED = {"channel_not_found", "not_in_channel", "is_archived"}


@dataclass(frozen=True)
class BreakoutCommand:
    instruction: str
    channel: str = ""
    channel_id: str = ""
    prior_text: str = ""

    @classmethod
    def parse(cls, text: str, bot_user_id: str) -> BreakoutCommand | None:
        """Parse a command immediately after the bot mention, or a bare command."""
        mentions = [f"<@{bot_user_id}>"] if bot_user_id else []
        if common.SLACK_BOT_USERNAME:
            mentions.append(f"@{common.SLACK_BOT_USERNAME}")
        match = None
        prior_text = ""
        if mentions:
            mention_re = re.compile("|".join(re.escape(mention) for mention in mentions))
            for mention in mention_re.finditer(text):
                candidate = _COMMAND_RE.fullmatch(text[mention.end() :].strip())
                if candidate is not None:
                    match = candidate
                    prior_text = text[: mention.start()].strip()
                    break
        if match is None:
            match = _COMMAND_RE.fullmatch(text.strip())
        if match is None:
            return None
        rest = (match.group("instruction") or "").strip()
        parts = rest.split(maxsplit=1)
        if not parts or not parts[0].startswith(("#", "<#")):
            return cls(instruction=rest, prior_text=prior_text)
        channel = _CHANNEL_MENTION_RE.fullmatch(parts[0])
        return cls(
            instruction=parts[1] if len(parts) > 1 else "",
            channel=parts[0],
            channel_id=channel["channel_id"] if channel else "",
            prior_text=prior_text,
        )


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


async def _mark_done(request: SlackRequest, target: str, new_ts: str) -> None:
    await mark_broken_out(
        request.channel_id, request.thread_ts, _command_ts(request), target, new_ts
    )


async def _tell_sender(request: SlackRequest, text: str) -> None:
    if request.user_id:
        await post_slack_ephemeral_message(
            request.channel_id, request.user_id, text, request.thread_ts
        )


def _post_failure(slack_error: str | None, target: str, fallback: str) -> str:
    if slack_error in _CHANNEL_REJECTED:
        return f"I can't post in <#{target}>. Add me to that channel, then try again."
    return fallback


async def _move(request: SlackRequest, target: str) -> None:
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
    heading = f"`/breakout`: {_title(title)}"
    result = await move_slack_thread(
        client,
        thread_id,
        active,
        target,
        await _root_text(request, heading),
    )
    new_ts = result.get("thread_ts")
    if not result.get("success") or not isinstance(new_ts, str):
        logger.warning(
            "Slack breakout move failed",
            extra={"agent_thread_id": thread_id, "move_error": result.get("error")},
        )
        await _tell_sender(
            request,
            _post_failure(
                result.get("slack_error"), target, "Could not move this thread; try again."
            ),
        )
        return
    await _mark_done(request, target, new_ts)


async def _start(
    request: SlackRequest,
    instruction: str,
    target: str,
    repo: common.SlackRepoResolution | None,
    prior_text: str = "",
    *,
    inherited_workspace: str | None = None,
) -> None:
    if (
        target != request.channel_id
        and request.thread_id
        and await common.thread_exists(request.thread_id)
    ):
        metadata = thread_metadata(await langgraph_client().threads.get(request.thread_id))
        if common.thread_is_private(metadata):
            await _tell_sender(request, "Private threads cannot be broken out to another channel.")
            return
    heading = f"`/breakout`: {_title(instruction)}"
    root_text = await _root_text(request, heading)
    try:
        new_ts = await post_slack_top_level_message_with_ts(
            target,
            root_text,
            blocks=block_payload(
                [
                    section(root_text),
                    *await origin_footer(
                        request.thread_id or "", (request.channel_id, request.thread_ts)
                    ),
                ]
            ),
            unfurl_links=False,
            unfurl_media=False,
        )
    except SlackRequestError as exc:
        logger.warning("Slack breakout root post failed", extra={"error": exc.code})
        await _tell_sender(
            request,
            _post_failure(exc.code, target, "Could not start a breakout thread; try again."),
        )
        return
    await _mark_done(request, target, new_ts)
    thread_id = await common.resolve_slack_thread_id(langgraph_client(), target, new_ts)
    web_url = dashboard_thread_url(thread_id)
    if web_url:
        try:
            await update_slack_message(
                target,
                new_ts,
                append_slack_web_link_footer(root_text, web_url),
                blocks=block_payload(
                    [
                        section(append_slack_web_link_footer(root_text, web_url)),
                        *await origin_footer(
                            request.thread_id or "", (request.channel_id, request.thread_ts)
                        ),
                    ]
                ),
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Slack breakout header web link update failed", extra={"slack_error": exc.code}
            )
    moved_channel = target != request.channel_id
    if moved_channel:
        repo = await common.get_slack_repo_config(
            target,
            new_ts,
            slack_user_id=request.user_id or None,
            thread_id=request.thread_id or thread_id,
        )
    await service.process_slack_mention(
        request.model_copy(
            update={
                "channel_id": target,
                "thread_ts": new_ts,
                "thread_id": thread_id,
                "text": instruction,
                "context_channel_id": request.channel_id,
                "context_thread_ts": request.thread_ts,
                "breakout_root_suffix": root_text[len(heading) :],
                "prior_message_text": prior_text,
                **(
                    {"channel_context": None, "concierge_mode": False, "reply_thread_ts": ""}
                    if moved_channel
                    else {}
                ),
            }
        ),
        repo,
        inherited_workspace=inherited_workspace,
    )


async def process_slack_breakout(
    request: SlackRequest, command: BreakoutCommand, repo: common.SlackRepoResolution | None
) -> None:
    """Move this thread when bare; otherwise start a new one seeded with this thread's transcript."""
    try:
        if command.channel and not command.channel_id:
            await _tell_sender(
                request,
                f"`{command.channel}` is not a Slack channel. Pick one from Slack's "
                "autocomplete: `/breakout #channel`.",
            )
            return
        workspace = (
            await common.get_thread_workspace(request.thread_id) if request.thread_id else None
        )
        destination = await resolve_breakout_destination(
            request.channel_id,
            command.channel_id,
            workspace=workspace,
            repo=repo.routing_repo if repo else None,
            tag=parse_workspace_tag(command.instruction)[0],
            login=await service.slack_login(request.user_id) if request.user_id else None,
        )
        target = destination.channel_id
        for channel_id in dict.fromkeys((request.channel_id, target)):
            channel = await SlackChannel.load(channel_id, use_cache=False)
            if channel is None or not channel.public:
                await _tell_sender(
                    request,
                    f"<#{channel_id}> is not a public channel. Breakouts only work "
                    "from and to public channels.",
                )
                return
        if command.instruction:
            await _start(
                request,
                command.instruction,
                target,
                repo,
                command.prior_text,
                inherited_workspace=destination.workspace if not command.channel_id else None,
            )
        else:
            await _move(request, target)
    except Exception:
        logger.exception("Slack breakout failed", extra={"agent_thread_id": request.thread_id})
        await _tell_sender(request, "Could not break out this thread; try again.")
