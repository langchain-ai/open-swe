from contextlib import suppress
from typing import Any, Literal

from langgraph.config import get_config

from agent.source_context import SourceContext

from ..utils.dashboard_links import dashboard_thread_url
from ..utils.slack import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    get_active_slack_thread,
    slack_thread_mutation_lock,
)
from ..utils.slack_code_channels import (
    CODE_CHANNEL_SESSION_TS,
    archive_code_channel,
    create_code_channel,
    is_code_channel_session,
    rename_session,
    set_context_bar,
    set_diff_view,
)
from ..utils.thread_ops import langgraph_client


async def manage_code_channel(
    action: Literal["create", "rename", "context", "view", "archive"],
    title: str = "",
    items: list[dict[str, Any]] | None = None,
    content: str = "",
    base_branch: str = "",
    head_branch: str = "",
    summary_message_ts: str = "",
) -> dict[str, Any]:
    """Manage the Slack code channel for this session.

    A code channel is a Slack channel dedicated to one task with you. Actions:

    - `create`: move this session into a new code channel opened from the current
      Slack thread. Pass `title` as the task headline. Only use it when the work
      deserves its own channel, and never when you are already in one.
    - `rename`: retitle the session with `title` once you know what the task is.
    - `context`: replace the context bar with `items` (max 5), each
      `{"key", "label", "icon", "url"}` — repo, branch, PR link, CI status.
    - `view`: publish a unified diff in the channel's diff tab. Pass `content`
      and optionally `base_branch`/`head_branch`. Publishing again replaces the
      tab in place.
    - `archive`: close the channel when the task is done. Post your closing
      summary with `slack_thread_reply` first and pass its `message_ts` as
      `summary_message_ts` so it survives archival.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "Missing thread_id in config"}

    client = langgraph_client()
    configured_slack = configurable.get("slack_thread")
    active = await get_active_slack_thread(
        client, thread_id, configured_slack if isinstance(configured_slack, dict) else None
    )
    if not active:
        return {"success": False, "error": "Current Slack location is unavailable"}
    channel_id = str(active.get("channel_id") or "")
    thread_ts = str(active.get("thread_ts") or "")

    if action == "create":
        if is_code_channel_session(thread_ts):
            return {"success": False, "error": "This session is already a code channel"}
        return await _create(client, thread_id, active, title)

    if not is_code_channel_session(thread_ts):
        return {"success": False, "error": "This session is not in a code channel"}

    if action == "rename":
        if not title.strip():
            return {"success": False, "error": "title is required"}
        ok, error = await rename_session(channel_id, title)
    elif action == "context":
        if not items:
            return {"success": False, "error": "items is required"}
        ok, error = await set_context_bar(channel_id, items)
    elif action == "view":
        if not content.strip():
            return {"success": False, "error": "content is required"}
        ok, error = await set_diff_view(
            channel_id,
            content,
            base_branch=base_branch,
            head_branch=head_branch,
        )
    elif action == "archive":
        ok, error = await archive_code_channel(
            channel_id, summary_message_ts=summary_message_ts.strip()
        )
    else:
        return {"success": False, "error": f"Unknown action {action}"}

    if not ok:
        return {"success": False, "error": error or "Slack rejected the request"}
    return {"success": True, "action": action, "channel_id": channel_id}


async def _create(
    client: Any, thread_id: str, active: dict[str, Any], title: str
) -> dict[str, Any]:
    if not title.strip():
        return {"success": False, "error": "title is required"}
    source_channel = str(active.get("channel_id") or "")
    source_ts = str(active.get("thread_ts") or "")
    origin_message_ts = str(active.get("triggering_event_ts") or "") or source_ts

    channel_id, error = await create_code_channel(
        name=title,
        session_id=thread_id,
        origin_channel_id=source_channel,
        origin_message_ts=origin_message_ts,
    )
    if not channel_id:
        return {"success": False, "error": error or "Slack could not create the code channel"}

    new_slack = {
        **{
            key: active.get(key, "")
            for key in ("triggering_user_id", "triggering_user_name", "triggering_user_email")
        },
        "channel_id": channel_id,
        "thread_ts": CODE_CHANNEL_SESSION_TS,
        "triggering_event_ts": origin_message_ts,
        "thread_version": 0,
    }
    bound = False
    try:
        async with slack_thread_mutation_lock(
            client, source_channel, source_ts, thread_id=thread_id
        ) as locked_active:
            if not locked_active or (
                locked_active.get("channel_id"),
                locked_active.get("thread_ts"),
            ) != (source_channel, source_ts):
                raise RuntimeError("Slack thread moved concurrently; retry")
            await bind_slack_thread_id(client, channel_id, CODE_CHANNEL_SESSION_TS, thread_id)
            bound = True
            await client.threads.update(
                thread_id=thread_id,
                metadata={
                    "source": "slack",
                    "source_context": SourceContext.parse({"slack_thread": new_slack}).dump(),
                },
            )
    except Exception as exc:  # noqa: BLE001
        # Leave no channel that still routes here: an orphan would keep
        # delivering messages into a session that never moved.
        if bound:
            with suppress(Exception):
                await delete_slack_thread_associations(
                    client, channel_id, CODE_CHANNEL_SESSION_TS, expected_thread_id=thread_id
                )
        with suppress(Exception):
            await archive_code_channel(channel_id)
        return {
            "success": False,
            "error": f"Could not bind the code channel to this session: {exc}",
            "retryable": True,
        }

    try:
        await delete_slack_thread_associations(
            client, source_channel, source_ts, expected_thread_id=thread_id
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"Code channel created but the source thread was not detached: {exc}",
            "channel_id": channel_id,
            "retryable": True,
        }

    return {
        "success": True,
        "action": "create",
        "channel_id": channel_id,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
