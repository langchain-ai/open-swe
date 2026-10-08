"""The review guide's own Slack messages, which the server posts and edits without a model turn.

- The progress message is the channel's summary message, which Slack shows in
  the thread the channel came from.
- A chunk's "Looks good" button is taken away once the chunk is settled, so only
  what is on screen ever offers it.
- A pull request update pauses the walkthrough with a note, until the reader
  says to go on.
"""

import logging
from typing import Literal

from openswe.review_guide.buttons import CONTINUE
from openswe.review_guide.diff import ChangedLine
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import Walk, summary
from openswe.slack.blocks import block_payload, context, option_actions
from openswe.slack.client import post_slack_top_level_message_with_ts, update_slack_message
from openswe.slack.code_channels import set_summary_message
from openswe.slack.http import SlackRequestError
from openswe.slack.markdown import markdown_blocks
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

Stage = Literal["starting", "walking", "paused", "finished", "ended"]
_HEADINGS: dict[Stage, str] = {
    "starting": "Starting the walkthrough",
    "walking": "Walkthrough in progress",
    "paused": "Walkthrough paused: the pull request changed",
    "finished": "Walkthrough finished",
    "ended": "Walkthrough ended",
}


def _text_blocks(
    text: str, *, note: str = "", buttons: list[str] | None = None
) -> list[JsonObject]:
    body = markdown_blocks(text) or []
    extra = [context(note)] if note else []
    return [*block_payload([*body, *extra]), *option_actions(buttons)]


async def refresh_progress(
    session: ReviewGuideSession,
    walk: Walk | None = None,
    unseen: list[ChangedLine] | None = None,
    *,
    stage: Stage = "walking",
) -> None:
    """Edit the progress message, posting it and making it the channel's summary the first time."""
    pr = session.pull_request
    lines = [f"*{_HEADINGS[stage]}* · <{pr.url}|{pr.repo}#{pr.number}>"]
    if walk is not None:
        lines.append(walk.coverage())
        left = walk.left(unseen or [])
        if left and stage in ("walking", "paused"):
            lines.append(f"{summary(left)}.")
    text = "\n".join(lines)
    channel_id = session.slack_channel_id
    if session.summary_message_ts:
        try:
            await update_slack_message(channel_id, session.summary_message_ts, text)
            return
        except SlackRequestError as exc:
            logger.warning(
                "Could not update a review guide's progress message",
                extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
            )
    try:
        message_ts = await post_slack_top_level_message_with_ts(channel_id, text)
    except SlackRequestError as exc:
        logger.warning(
            "Could not post a review guide's progress message",
            extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
        )
        return
    await session.save_summary_message(message_ts)
    try:
        await set_summary_message(channel_id, message_ts)
    except SlackRequestError as exc:
        logger.warning(
            "Could not set a review guide's summary message",
            extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
        )


async def post_with_buttons(session: ReviewGuideSession, text: str, buttons: list[str]) -> str:
    """Post a message the guide prepared earlier; its timestamp, or ``""`` when Slack refused."""
    try:
        return await post_slack_top_level_message_with_ts(
            session.slack_channel_id, text, blocks=_text_blocks(text, buttons=buttons)
        )
    except SlackRequestError as exc:
        logger.warning(
            "Could not post a prepared review guide chunk",
            extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
        )
        return ""


async def retire(channel_id: str, message_ts: str, text: str, note: str) -> None:
    """Take the buttons off a message the guide posted, saying why."""
    if not message_ts:
        return
    try:
        await update_slack_message(
            channel_id, message_ts, text, blocks=_text_blocks(text, note=note)
        )
    except SlackRequestError as exc:
        logger.warning(
            "Could not take the buttons off a review guide message",
            extra={"slack_channel": channel_id, "slack_error": str(exc)},
        )


async def pause(session: ReviewGuideSession, head_sha: str) -> None:
    """Tell the reader the pull request changed, and wait for them to go on."""
    text = (
        f"The pull request was updated (now at `{head_sha[:7]}`), so I've paused the "
        "walkthrough. Tell me when to continue and I'll pick up with what changed."
    )
    blocks = _text_blocks(text, buttons=[CONTINUE])
    channel_id = session.slack_channel_id
    if session.paused_message_ts:
        try:
            await update_slack_message(channel_id, session.paused_message_ts, text, blocks=blocks)
            return
        except SlackRequestError as exc:
            logger.warning(
                "Could not update a review guide's pause note",
                extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
            )
    try:
        message_ts = await post_slack_top_level_message_with_ts(channel_id, text, blocks=blocks)
    except SlackRequestError as exc:
        logger.warning(
            "Could not post a review guide's pause note",
            extra={"agent_thread_id": session.thread_id, "slack_error": str(exc)},
        )
        return
    await session.save_pause(message_ts)


async def resume(session: ReviewGuideSession) -> None:
    """Clear the pause, taking the button off its note if the reader went on by typing."""
    if not session.paused_message_ts:
        return
    await retire(
        session.slack_channel_id,
        session.paused_message_ts,
        "The pull request was updated while the walkthrough was paused.",
        "Resumed",
    )
    await session.save_pause("")
