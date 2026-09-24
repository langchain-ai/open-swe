"""Rebind an Open SWE thread from one Slack location to another."""

import logging
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
from agent.slack.code_channels import is_code_channel_session
from agent.slack.thinking import release_slack_location_status, sync_slack_background_status
from agent.source_context import SlackThreadRef, SourceContext
from agent.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)


class SlackRebindError(Exception):
    """A failed rebind; `moved` is whether the thread already points at the destination."""

    def __init__(self, message: str, *, moved: bool) -> None:
        super().__init__(message)
        self.moved = moved


def _slack_error_hint(error: str | None) -> str:
    if error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the destination channel; verify the channel ID and bot access."
    if error and error.startswith("rate_limited"):
        return "Slack rate limited the request; wait before retrying."
    if error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry."
    return "Slack could not create the destination thread; retry once."


def _new_slack_context(current: SlackThreadRef, channel_id: str, thread_ts: str) -> SlackThreadRef:
    return SlackThreadRef.model_validate(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "triggering_user_id": current.triggering_user_id,
            "triggering_user_name": current.triggering_user_name,
            "triggering_user_email": current.triggering_user_email,
            "triggering_event_ts": thread_ts,
            **{
                key: getattr(current, key)
                for key in ("team_id", "triggering_bot_id", "triggering_bot_app_id")
                if key in current.model_fields_set
            },
        }
    )


async def _current_location(client: LangGraphClient, thread_id: str) -> tuple[str, str] | None:
    active = await get_active_slack_thread(client, thread_id)
    return SlackThreadRef.model_validate(active).location if active else None


async def _carry_run_mapping(
    client: LangGraphClient, source: SlackThreadRef, destination: SlackThreadRef
) -> None:
    source_run = await lookup_slack_thread_run_mapping(client, source.channel_id, source.thread_ts)
    if not isinstance(source_run, Mapping):
        return
    run_id = source_run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return
    await store_slack_run_mapping(
        client,
        destination.channel_id,
        destination.thread_ts,
        run_id,
        message_ts=(
            None if is_code_channel_session(destination.thread_ts) else destination.thread_ts
        ),
        triggering_user_id=(
            str(source_run.get("triggering_user_id"))
            if source_run.get("triggering_user_id")
            else None
        ),
    )


async def rebind_slack_thread(
    client: LangGraphClient,
    thread_id: str,
    source: SlackThreadRef,
    destination: SlackThreadRef,
) -> None:
    """Point the thread at `destination` and carry everything keyed to `source` along.

    Finishes a rebind whose metadata already points at `destination`, so a retry
    after a partial failure completes instead of reporting a concurrent move.
    """
    source_location = (source.channel_id, source.thread_ts)
    target_location = (destination.channel_id, destination.thread_ts)
    bound_here = False
    try:
        async with slack_thread_mutation_lock(
            client, *source_location, thread_id=thread_id
        ) as locked_active:
            current = (
                SlackThreadRef.model_validate(locked_active).location if locked_active else None
            )
            if current == target_location:
                await bind_slack_thread_id(client, *target_location, thread_id)
            elif current == source_location:
                await bind_slack_thread_id(client, *target_location, thread_id)
                bound_here = True
                await client.threads.update(
                    thread_id=thread_id,
                    metadata={
                        "source": "slack",
                        "source_context": SourceContext(slack_thread=destination).dump(),
                    },
                )
                if await _current_location(client, thread_id) != target_location:
                    raise RuntimeError("destination metadata did not persist")
            else:
                raise RuntimeError("Slack thread moved concurrently; retry")
    except Exception as exc:
        if bound_here:
            try:
                await delete_slack_thread_associations(
                    client, *target_location, expected_thread_id=thread_id
                )
            except Exception:
                logger.warning(
                    "Could not roll back a Slack rebind destination",
                    extra={"agent_thread_id": thread_id, "slack_channel": target_location[0]},
                    exc_info=True,
                )
        raise SlackRebindError(str(exc), moved=False) from exc

    try:
        await _carry_run_mapping(client, source, destination)
        await release_slack_location_status(client, *source_location)
        await sync_slack_background_status(client, thread_id, resume=True)
        await delete_slack_thread_associations(
            client, *source_location, expected_thread_id=thread_id
        )
    except Exception as exc:
        raise SlackRebindError(str(exc), moved=True) from exc


async def move_slack_thread(
    client: LangGraphClient,
    thread_id: str,
    source: Mapping[str, Any],
    target_channel: str,
    message: str,
) -> dict[str, Any]:
    """Post `message` as a new root in `target_channel` and move the thread's Slack binding there."""
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

    source_ref = SlackThreadRef.model_validate(dict(source))
    try:
        await rebind_slack_thread(
            client, thread_id, source_ref, _new_slack_context(source_ref, target_channel, new_ts)
        )
    except SlackRebindError as exc:
        if exc.moved:
            return {
                "success": False,
                "error": f"Move cleanup failed: {exc}",
                "retryable": True,
                "channel_id": target_channel,
                "thread_ts": new_ts,
            }
        return {
            "success": False,
            "error": f"Could not persist Slack move: {exc}",
            "retryable": True,
        }

    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": target_channel,
        "thread_ts": new_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
