"""Moving a Slack conversation MDA's Slack channel holds, as the run on its thread.

The run posts the new root through MDA, ``post(..., new_thread=True, hand_off=True)``,
which hands the run's own thread to the new Slack thread. The backend checks the move
first (:func:`prepare_channel_move`), as the ``slack_move_thread`` tool does, and records
where the thread went afterwards (:func:`record_channel_move`). Both run as the run its
token names, so the thread is the run's own. A move into another Slack channel posts
there through MDA too (``to=``); a thread the backend holds moves with the backend's
tool, which posts with the bot token, as before.
"""

from collections.abc import Mapping
from typing import Any, cast

from langgraph.config import get_config
from pydantic import JsonValue
from runtime_api.slack import SlackMoved, SlackMovePlan, SlackMoveRequest, SlackMoveResult

from openswe.dispatch import channel_of
from openswe.slack import mda_forwarding
from openswe.slack.breakout import BreakoutCommand
from openswe.slack.breakout_links import mark_broken_out, source_thread_line
from openswe.slack.client import append_slack_web_link_footer, get_active_slack_thread
from openswe.slack.move import record_slack_move
from openswe.slack.tools.move_thread import CheckedMove, check_move
from openswe.threads import index
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.threads.summary import thread_is_private
from openswe.utils.dashboard_links import dashboard_thread_url


def _answer(result: Mapping[str, Any]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], dict(result))


async def prepare_channel_move(request: SlackMoveRequest) -> SlackMovePlan:
    """What the run posts to move its conversation, and whether it posts it through MDA.

    Without a message it's a person's bare ``/breakout``: the root names the thread, the
    command and who sent it, as the backend's own move does.
    """
    message = request.message
    if message is None:
        message = await _command_root()
    checked = await check_move(message, request.channel_id)
    if not isinstance(checked, CheckedMove):
        return SlackMovePlan(result=_answer(checked))
    indexed = await index.get(checked.thread_id)
    held = indexed is not None and channel_of(indexed.metadata) == mda_forwarding.CHANNEL
    same_channel = checked.target_channel == str(checked.active.get("channel_id") or "")
    return SlackMovePlan(
        root_text=append_slack_web_link_footer(
            checked.message, dashboard_thread_url(checked.thread_id)
        ),
        via_mda=held,
        channel_id=None if same_channel else checked.target_channel,
    )


async def record_channel_move(request: SlackMoved) -> SlackMoveResult:
    """Point the run's thread at the root the run posted through MDA.

    The root is in the conversation's own Slack channel, or in ``request.channel_id``.
    """
    configurable = get_config().get("configurable", {})
    thread_id = configurable.get("thread_id")
    configured_slack = configurable.get("slack_thread")
    if not isinstance(thread_id, str) or not thread_id:
        return SlackMoveResult(result={"success": False, "error": "Missing thread_id in config"})
    if not isinstance(configured_slack, Mapping):
        return SlackMoveResult(result={"success": False, "error": "Missing slack_thread config"})
    active = await get_active_slack_thread(thread_id, configured_slack)
    if not active:
        return SlackMoveResult(
            result={"success": False, "error": "Current Slack location is unavailable"}
        )
    channel_id = str(active.get("channel_id") or "")
    source_ts = str(active.get("thread_ts") or "")
    target_channel = request.channel_id or channel_id
    if target_channel != channel_id and (
        (thread := await THREADS.get(thread_id)) is None or thread_is_private(thread.metadata)
    ):
        return SlackMoveResult(
            result={"success": False, "error": "Private threads cannot move to another channel"}
        )
    # MDA handed the thread to the new Slack thread: its writes act in that conversation now.
    RUNTIMES.mda_runtime.forget_channel_conversation(thread_id)
    result = await record_slack_move(thread_id, active, target_channel, request.thread_ts)
    if result.get("success") and request.mark_source and source_ts != request.thread_ts:
        await mark_broken_out(channel_id, source_ts, target_channel, request.thread_ts)
    return SlackMoveResult(result=_answer(result))


async def _command_root() -> str:
    """A bare ``/breakout``'s root: the thread's title, a link to the command, its sender."""
    configurable = get_config().get("configurable", {})
    thread_id = configurable.get("thread_id")
    slack_thread = configurable.get("slack_thread")
    slack = slack_thread if isinstance(slack_thread, Mapping) else {}
    title = ""
    if isinstance(thread_id, str) and thread_id:
        thread = await THREADS.get(thread_id)
        title = str((thread.metadata if thread else {}).get("title") or "").strip()
    channel_id = str(slack.get("channel_id") or "")
    command_ts = str(slack.get("triggering_event_ts") or slack.get("thread_ts") or "")
    user_id = str(slack.get("triggering_user_id") or "")
    parts = (
        BreakoutCommand.heading(title or "Untitled"),
        await source_thread_line(channel_id, command_ts) if channel_id and command_ts else "",
        f"<@{user_id}>" if user_id else "",
    )
    return " · ".join(part for part in parts if part)
