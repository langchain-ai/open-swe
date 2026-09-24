"""Rebind an Open SWE thread from one Slack thread to a new top-level Slack thread."""

from collections.abc import Mapping
from typing import Any

from langgraph_sdk.client import LangGraphClient

from agent.slack.client import (
    append_slack_web_link_footer,
    bind_slack_thread_id,
    delete_slack_thread_associations,
    get_active_slack_thread,
    lookup_slack_thread_run_mapping,
    post_slack_top_level_message_with_ts,
    slack_thread_mutation_lock,
    store_slack_run_mapping,
)
from agent.source_context import SourceContext
from agent.utils.dashboard_links import dashboard_thread_url


def _slack_error_hint(error: str | None) -> str:
    if error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the destination channel; verify the channel ID and bot access."
    if error and error.startswith("rate_limited"):
        return "Slack rate limited the request; wait before retrying."
    if error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry."
    return "Slack could not create the destination thread; retry once."


def _new_slack_context(
    current: Mapping[str, Any], channel_id: str, thread_ts: str
) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "triggering_user_id": current.get("triggering_user_id", ""),
        "triggering_user_name": current.get("triggering_user_name", ""),
        "triggering_user_email": current.get("triggering_user_email", ""),
        "triggering_event_ts": thread_ts,
        **{
            key: current[key]
            for key in ("team_id", "triggering_bot_id", "triggering_bot_app_id")
            if key in current
        },
    }


async def move_slack_thread(
    client: LangGraphClient,
    thread_id: str,
    source: Mapping[str, Any],
    target_channel: str,
    message: str,
) -> dict[str, Any]:
    """Post `message` as a new root in `target_channel` and move the thread's Slack binding there."""
    source_channel = str(source.get("channel_id") or "")
    source_ts = str(source.get("thread_ts") or "")
    root_text = append_slack_web_link_footer(message, dashboard_thread_url(thread_id))
    new_ts, slack_error = await post_slack_top_level_message_with_ts(
        target_channel,
        root_text,
        unfurl_links=False,
        unfurl_media=False,
    )
    if not new_ts:
        return {
            "success": False,
            "error": slack_error or "Slack post failed",
            "slack_error": slack_error,
            "hint": _slack_error_hint(slack_error),
        }

    new_slack = _new_slack_context(source, target_channel, new_ts)
    destination_bound = False
    try:
        async with slack_thread_mutation_lock(
            client, source_channel, source_ts, thread_id=thread_id
        ) as locked_active:
            if not locked_active or (
                locked_active.get("channel_id"),
                locked_active.get("thread_ts"),
            ) != (source_channel, source_ts):
                raise RuntimeError("Slack thread moved concurrently; retry")
            await bind_slack_thread_id(client, target_channel, new_ts, thread_id)
            destination_bound = True
            await client.threads.update(
                thread_id=thread_id,
                metadata={
                    "source": "slack",
                    "source_context": SourceContext.parse({"slack_thread": new_slack}).dump(),
                },
            )
            persisted = await get_active_slack_thread(client, thread_id)
            if not persisted or (persisted.get("channel_id"), persisted.get("thread_ts")) != (
                target_channel,
                new_ts,
            ):
                raise RuntimeError("destination metadata did not persist")
    except Exception as exc:  # noqa: BLE001
        if destination_bound:
            try:
                await delete_slack_thread_associations(
                    client,
                    target_channel,
                    new_ts,
                    expected_thread_id=thread_id,
                )
            except Exception:  # noqa: BLE001
                pass
        return {
            "success": False,
            "error": f"Could not persist Slack move: {exc}",
            "retryable": True,
        }

    source_run = await lookup_slack_thread_run_mapping(client, source_channel, source_ts)
    if isinstance(source_run, Mapping):
        run_id = source_run.get("run_id")
        if isinstance(run_id, str) and run_id:
            await store_slack_run_mapping(
                client,
                target_channel,
                new_ts,
                run_id,
                message_ts=new_ts,
                triggering_user_id=(
                    str(source_run.get("triggering_user_id"))
                    if source_run.get("triggering_user_id")
                    else None
                ),
            )

    try:
        await delete_slack_thread_associations(
            client,
            source_channel,
            source_ts,
            expected_thread_id=thread_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"Move cleanup failed: {exc}",
            "retryable": True,
            "channel_id": target_channel,
            "thread_ts": new_ts,
        }

    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": target_channel,
        "thread_ts": new_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
