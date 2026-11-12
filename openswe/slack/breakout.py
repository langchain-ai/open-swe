"""`@Open SWE /breakout`, handled without waking the current thread's agent."""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from uuid import uuid4

from openswe.slack import mda_forwarding
from openswe.slack import webhook as service
from openswe.slack.blocks import block_payload, section
from openswe.slack.breakout_destination import resolve_breakout_destination
from openswe.slack.breakout_links import (
    breakout_root_blocks,
    mark_broken_out,
    notify_web_breakout,
    source_thread_line,
)
from openswe.slack.client import (
    append_slack_web_link_footer,
    get_active_slack_thread,
    post_slack_ephemeral_message,
    post_slack_top_level_message_with_ts,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.move import move_slack_thread
from openswe.slack.parsed_message import ParsedSlackMessage, SlackAction
from openswe.slack.records import bind_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks import common

logger = logging.getLogger(__name__)

_CHANNEL_MENTION_RE = re.compile(r"<#(?P<channel_id>[CG][A-Z0-9]+)(?:\|[^>]*)?>")
_TITLE_MAX_CHARS = 160
_CHANNEL_REJECTED = {"channel_not_found", "not_in_channel", "is_archived"}

#: Tells the sender why a breakout opened nothing.
type Tell = Callable[[SlackRequest, str], Awaitable[None]]

#: Hands a new breakout thread's first turn to MDA's Slack channel.
type HandToMda = Callable[[SlackRequest, common.SlackRepoResolution | None], Awaitable[None]]


@dataclass(frozen=True)
class BreakoutCommand:
    instruction: str
    channel: str = ""
    channel_id: str = ""
    prior_text: str = ""
    options: str = ""
    workspace: str | None = None

    @classmethod
    def from_message(cls, message: ParsedSlackMessage) -> BreakoutCommand:
        command = cls(
            instruction=message.argument,
            prior_text=message.prior_text,
            options=message.options,
            workspace=message.workspace,
        )
        parts = message.argument.split(maxsplit=1)
        if parts and parts[0].lower() == "in" and len(parts) > 1:
            destination = parts[1].split(maxsplit=1)
            if destination[0].startswith(("#", "<#")):
                parts = destination
        if (
            message.action is not SlackAction.BREAKOUT
            or not parts
            or not parts[0].startswith(("#", "<#"))
        ):
            return command
        channel = _CHANNEL_MENTION_RE.fullmatch(parts[0])
        return replace(
            command,
            instruction=parts[1] if len(parts) > 1 else "",
            channel=parts[0],
            channel_id=channel["channel_id"] if channel else "",
        )

    @property
    def thread_text(self) -> str:
        """The new thread's opening message, keeping options typed alongside the command."""
        return f"{self.options} {self.instruction}".strip()

    @classmethod
    def heading(cls, title: str) -> str:
        first_line = title.splitlines()[0].strip() if title.strip() else ""
        if len(first_line) > _TITLE_MAX_CHARS:
            first_line = f"{first_line[: _TITLE_MAX_CHARS - 1].rstrip()}…"
        return f"`/breakout`: {first_line}"


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
    await mark_broken_out(request.channel_id, request.thread_ts, target, new_ts)


async def _tell_sender(request: SlackRequest, text: str) -> None:
    if request.user_id:
        await post_slack_ephemeral_message(
            request.channel_id, request.user_id, text, request.thread_ts
        )


def _post_failure(slack_error: str | None, target: str, fallback: str) -> str:
    if slack_error in _CHANNEL_REJECTED:
        return f"I can't post in <#{target}>. Add me to that channel, then try again."
    return fallback


async def _move(request: SlackRequest, target: str, tell: Tell) -> None:
    thread_id = request.thread_id
    if not thread_id or not await common.thread_exists(thread_id):
        await tell(request, "There is nothing here to break out yet.")
        return
    client = langgraph_client()
    metadata = thread_metadata(await client.threads.get(thread_id))
    if common.thread_is_private(metadata):
        await tell(request, "Private threads cannot be broken out.")
        return
    active = await get_active_slack_thread(thread_id)
    if not active or (active.get("channel_id"), active.get("thread_ts")) != (
        request.channel_id,
        request.thread_ts,
    ):
        await tell(request, "This thread already moved to another Slack thread.")
        return

    title = str(metadata.get("title") or "").strip() or "Untitled"
    heading = BreakoutCommand.heading(title)
    result = await move_slack_thread(
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
        await tell(
            request,
            _post_failure(
                result.get("slack_error"), target, "Could not move this thread; try again."
            ),
        )
        return
    await _mark_done(request, target, new_ts)


async def _start(
    request: SlackRequest,
    command: BreakoutCommand,
    target: str,
    repo: common.SlackRepoResolution | None,
    *,
    inherited_workspace: str | None = None,
    hand_to_mda: HandToMda | None = None,
    branches: bool = False,
    tell: Tell,
) -> None:
    if (
        target != request.channel_id
        and request.thread_id
        and await common.thread_exists(request.thread_id)
    ):
        metadata = thread_metadata(await langgraph_client().threads.get(request.thread_id))
        if common.thread_is_private(metadata):
            await tell(request, "Private threads cannot be broken out to another channel.")
            return
    heading = BreakoutCommand.heading(command.instruction)
    root_text = await _root_text(request, heading)
    try:
        new_ts = await post_slack_top_level_message_with_ts(
            target,
            root_text,
            blocks=block_payload([section(root_text)]),
            unfurl_links=False,
            unfurl_media=False,
        )
    except SlackRequestError as exc:
        logger.warning("Slack breakout root post failed", extra={"error": exc.code})
        await tell(
            request,
            _post_failure(exc.code, target, "Could not start a breakout thread; try again."),
        )
        return
    await _mark_done(request, target, new_ts)
    held_thread_id = ""
    if hand_to_mda is None:
        thread_id = await common.resolve_slack_thread_id(target, new_ts)
    else:
        # MDA's channel runs it on a thread of the command's own, which the new Slack thread
        # joins. With ``branches``, the channel starts it with what this conversation's
        # thread holds; otherwise it is seeded from the Slack thread.
        owner_login = await service.slack_login(request.user_id) if request.user_id else None
        thread_id = mda_forwarding.side_thread_id(request.channel_id, _command_ts(request))
        held_thread_id = (request.thread_id or "") if branches else ""
        await bind_slack_thread_id(target, new_ts, thread_id)
        await mda_forwarding.claim_side(thread_id, owner_login=owner_login)
    web_url = dashboard_thread_url(thread_id)
    if web_url:
        try:
            await update_slack_message(
                target,
                new_ts,
                append_slack_web_link_footer(root_text, web_url),
                blocks=block_payload(breakout_root_blocks(root_text, web_url)),
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
    opening = request.model_copy(
        update={
            "channel_id": target,
            "thread_ts": new_ts,
            "thread_id": thread_id,
            "held_thread_id": held_thread_id,
            "text": command.thread_text,
            "context_channel_id": request.channel_id,
            "context_thread_ts": request.thread_ts,
            "breakout_root_suffix": root_text[len(heading) :],
            "prior_message_text": command.prior_text,
            "inherited_workspace": inherited_workspace,
            **(
                {"channel_context": None, "concierge_mode": False, "reply_thread_ts": ""}
                if moved_channel
                else {}
            ),
        }
    )
    if hand_to_mda is not None:
        await hand_to_mda(opening, repo)
        return
    await service.process_slack_mention(opening, repo, inherited_workspace=inherited_workspace)


async def web_opening(
    request: SlackRequest, command: BreakoutCommand, thread_id: str
) -> SlackRequest:
    """The first turn of the web conversation a ``/breakout:web`` starts on ``thread_id``,
    seeded with the Slack thread it came from and that thread's workspace."""
    return request.model_copy(
        update={
            "thread_id": thread_id,
            "text": command.thread_text,
            "context_channel_id": request.channel_id,
            "context_thread_ts": request.thread_ts,
            "prior_message_text": command.prior_text,
            "web_only": True,
            "concierge_mode": False,
            "reply_thread_ts": "",
            "inherited_workspace": (
                await common.get_thread_workspace(request.thread_id) if request.thread_id else None
            ),
        }
    )


async def process_slack_web(
    request: SlackRequest, command: BreakoutCommand, repo: common.SlackRepoResolution | None
) -> None:
    """Start an unattached web conversation with the source Slack transcript."""
    if not command.instruction:
        await _tell_sender(request, "Add a question after `@Robin /breakout:web`.")
        return
    thread_id = str(uuid4())
    try:
        opening = await web_opening(request, command, thread_id)
        started = await service.process_slack_web_mention(
            opening, repo, inherited_workspace=opening.inherited_workspace
        )
        if started:
            await notify_web_breakout(opening)
    except Exception:
        logger.exception("Slack web question failed", extra={"agent_thread_id": thread_id})
        await _tell_sender(request, "Could not start a web conversation; try again.")


async def process_slack_breakout(
    request: SlackRequest,
    command: BreakoutCommand,
    repo: common.SlackRepoResolution | None,
    *,
    hand_to_mda: HandToMda | None = None,
    branches: bool = False,
    tell: Tell = _tell_sender,
) -> None:
    """Move this thread when bare; otherwise start a new one seeded with this thread's transcript.

    With ``hand_to_mda``, a new thread's first turn goes to MDA's Slack channel, which
    runs the new Slack thread from then on; moving a thread stays here. ``branches``
    says the channel starts that thread with what this conversation's thread holds.
    ``tell`` gives the sender the reason when it opens nothing, privately by default.
    """
    try:
        if command.channel and not command.channel_id:
            await tell(
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
            tag=command.workspace,
            login=await service.slack_login(request.user_id) if request.user_id else None,
        )
        target = destination.channel_id
        if error := await destination.channel_error(request.channel_id):
            await tell(request, error)
            return
        if command.instruction:
            await _start(
                request,
                command,
                target,
                repo,
                inherited_workspace=destination.workspace if not command.channel_id else None,
                hand_to_mda=hand_to_mda,
                branches=branches,
                tell=tell,
            )
        else:
            await _move(request, target, tell)
    except Exception:
        logger.exception("Slack breakout failed", extra={"agent_thread_id": request.thread_id})
        await tell(request, "Could not break out this thread; try again.")
