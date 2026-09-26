"""Agent tool for posting a Slack modal intake form."""

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.slack.forms import FormItem, create_form


async def slack_open_modal(title: str, items: list[FormItem]) -> dict[str, str | bool]:
    """Post an Open form button in the current Slack conversation."""
    cfg = RunConfig.from_config(get_config())
    origin = cfg.slack_thread
    if not origin or not origin.channel_id or not origin.thread_ts or not cfg.thread_id:
        return {"success": False, "error": "slack_thread is required"}
    if not origin.triggering_user_id:
        return {"success": False, "error": "Slack submitter is required"}
    if not title.strip() or len(title) > 24 or not items or len(items) > 9:
        return {
            "success": False,
            "error": "Title must be 1-24 characters and items must contain 1-9 entries",
        }
    if any(not item.label.strip() for item in items):
        return {"success": False, "error": "Item labels cannot be empty"}
    record, message_ts = await create_form(
        title,
        items,
        origin.channel_id,
        origin.thread_ts,
        cfg.thread_id,
        origin.triggering_user_id,
        origin.reply_thread_ts,
    )
    if not message_ts:
        return {"success": False, "error": "Could not post form"}
    return {"success": True, "form_id": record.id, "message_ts": message_ts}
