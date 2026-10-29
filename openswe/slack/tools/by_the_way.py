"""The tools that finish a `/btw` question in the asker's DM (``openswe.slack.by_the_way``)."""

from typing import Any

from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.by_the_way import ByTheWayDm
from openswe.slack.client import SlackThreadMappingError, get_slack_permalink
from openswe.slack.dm import post_dm
from openswe.slack.http import SlackRequestError
from openswe.slack.records import bind_slack_thread_id
from openswe.slack.tools.reply import _by_the_way_reply  # noqa: PLC2701


async def slack_btw_ask_in_dm(message: str) -> dict[str, Any]:
    """Implement the `slack_btw_ask_in_dm` tool."""
    cfg = RunConfig.from_runtime()
    asked_in = cfg.slack_by_the_way_thread_ts or ""
    if cfg.slack_ask is not True or not asked_in or cfg.slack_thread is None or not cfg.thread_id:
        return {"success": False, "error": "Only a /btw question continues in a DM"}
    if not message.strip():
        return {"success": False, "error": "message is required"}
    if await ByTheWayDm.of(cfg.thread_id) is not None:
        return {"success": False, "error": "This question already continues in a DM"}
    channel_id = cfg.slack_thread.channel_id
    permalink = await get_slack_permalink(channel_id, asked_in) or ""
    try:
        dm_channel, dm_ts = await post_dm(
            cfg.slack_thread.triggering_user_id,
            prompt("slack/btw-dm-question", question=message.strip(), permalink=permalink),
        )
        # Recorded before the DM thread is bound, so its first reply already finds it.
        await ByTheWayDm(
            channel_id=dm_channel, thread_ts=dm_ts, question=message.strip(), permalink=permalink
        ).save(cfg.thread_id)
        await bind_slack_thread_id(dm_channel, dm_ts, cfg.thread_id)
    except (SlackRequestError, SlackThreadMappingError) as exc:
        return {"success": False, "error": f"Could not continue in a DM: {exc}"}
    return {"success": True, "hint": "The asker answers in the DM; post nothing else, end the run."}


async def slack_btw_answer(message: str) -> dict[str, Any]:
    """Implement the `slack_btw_answer` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id or ""
    found = await ByTheWayDm.of(thread_id) if thread_id else None
    here = cfg.slack_thread
    if found is None or here is None:
        return {"success": False, "error": "No /btw question is waiting for its answer here"}
    dm, asked_in = found
    if dm.answered or (dm.channel_id, dm.thread_ts) != (here.channel_id, here.thread_ts):
        return {"success": False, "error": "No /btw question is waiting for its answer here"}
    # Answered as the `/btw` run itself would: where, and how, its source context says.
    as_asked = RunConfig.parse({**cfg.dump(), **asked_in.dump()})
    result = await _by_the_way_reply(
        as_asked, asked_in.slack_by_the_way_thread_ts or "", message, "final", None, None
    )
    if result.get("success") is True or result.get("error") == "the /btw answer was already posted":
        await dm.model_copy(update={"answered": True}).save(thread_id)
    return result
