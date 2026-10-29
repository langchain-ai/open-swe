"""The tools that finish a `/btw` question in the asker's DM (``openswe.slack.by_the_way``)."""

import logging
from typing import Any

from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.by_the_way import ByTheWayDm
from openswe.slack.client import SlackThreadMappingError, get_slack_permalink
from openswe.slack.dm import post_dm
from openswe.slack.http import SlackRequestError
from openswe.slack.records import bind_slack_thread_id
from openswe.slack.tools.reply import post_by_the_way_answer
from openswe.source_context import SourceContext
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_QUESTION_MAX_CHARS = 2800
_ALREADY_POSTED = "the /btw answer was already posted"


async def slack_btw_ask_in_dm(message: str) -> dict[str, Any]:
    """Implement the `slack_btw_ask_in_dm` tool."""
    cfg = RunConfig.from_runtime()
    slack = cfg.slack_thread
    thread_id = cfg.thread_id or ""
    asked_in = cfg.slack_by_the_way_thread_ts or ""
    if cfg.slack_ask is not True or not asked_in or slack is None or not thread_id:
        return {"success": False, "error": "Only a /btw question continues in a DM"}
    question = message.strip()
    if not question:
        return {"success": False, "error": "message is required"}
    if len(question) > _QUESTION_MAX_CHARS:
        return {"success": False, "error": "message is too long", "max_chars": _QUESTION_MAX_CHARS}
    client = langgraph_client()
    if ByTheWayDm.of(thread_metadata(await client.threads.get(thread_id))) is not None:
        return {
            "success": False,
            "error": "This question already continues in a DM",
            "hint": "End the run; the asker answers there.",
        }
    permalink = (
        await get_slack_permalink(slack.channel_id, cfg.slack_by_the_way_message_ts or asked_in)
        or ""
    )
    try:
        dm_channel, dm_ts = await post_dm(
            slack.triggering_user_id,
            prompt("slack/btw-dm-question", question=question, permalink=permalink),
        )
    except SlackRequestError as exc:
        return {"success": False, "error": exc.code or "DM failed", "slack_error": exc.code}
    # Recorded before the DM thread is bound, so its first reply already finds the question.
    await ByTheWayDm(
        channel_id=dm_channel, thread_ts=dm_ts, question=question, permalink=permalink
    ).save(thread_id)
    try:
        await bind_slack_thread_id(dm_channel, dm_ts, thread_id)
    except SlackThreadMappingError:
        logger.exception(
            "Could not bind the /btw DM to its thread", extra={"agent_thread_id": thread_id}
        )
        return {"success": False, "error": "Could not link the DM to this question"}
    return {
        "success": True,
        "hint": (
            "The asker answers in the DM, which continues this thread. "
            "Post nothing in the thread the question was asked in; end the run."
        ),
    }


async def slack_btw_answer(message: str) -> dict[str, Any]:
    """Implement the `slack_btw_answer` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id or ""
    client = langgraph_client()
    metadata = thread_metadata(await client.threads.get(thread_id)) if thread_id else {}
    dm = ByTheWayDm.of(metadata)
    here = cfg.slack_thread
    if dm is None or dm.answered or here is None or not dm.is_at(here.channel_id, here.thread_ts):
        return {"success": False, "error": "No /btw question is waiting for its answer here"}
    asked_in = SourceContext.from_metadata(metadata)
    result = await post_by_the_way_answer(
        thread_id,
        asked_in.slack_thread.channel_id if asked_in.slack_thread else "",
        asked_in.slack_by_the_way_thread_ts or "",
        asked_in.slack_by_the_way_message_ts,
        message,
    )
    if result.get("success") is True or result.get("error") == _ALREADY_POSTED:
        # Detached: the DM is the asker's own conversation from here on.
        await dm.model_copy(update={"answered": True}).save(thread_id)
    if result.get("success") is True:
        result["hint"] = "Posted. Tell the asker in this DM, with a link to the thread."
    return result
