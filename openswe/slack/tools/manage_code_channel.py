import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Literal

from openswe.run_config import RunConfig
from openswe.slack.client import (
    get_active_slack_thread,
    invite_to_slack_channel,
    slack_user_ids,
)
from openswe.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    DEFAULT_CODE_CHANNEL_COMMANDS,
    VIEW_CONTENT_MAX_BYTES,
    CanvasAccessLevel,
    SessionStatus,
    ViewType,
    archive_code_channel,
    block_suggestions_error,
    create_code_channel,
    delete_block_suggestions,
    get_canvas,
    is_code_channel_session,
    list_views,
    remove_view,
    rename_session,
    repo_context_bar_items,
    set_agent_resource,
    set_canvas_content,
    set_commands,
    set_context_bar,
    set_session_status_result,
    set_summary_message,
    set_view,
    store_block_suggestions,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.move import SlackRebindError, rebind_slack_thread
from openswe.source_context import SlackThreadRef
from openswe.threads.summary import thread_is_private
from openswe.tools.create_sandbox_file_download_url import resolve_sandbox_file
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


async def manage_code_channel(
    action: Literal[
        "create",
        "status",
        "rename",
        "context",
        "summary",
        "resource",
        "commands",
        "view",
        "list_views",
        "remove_view",
        "get_canvas",
        "set_canvas",
        "archive",
    ],
    title: str = "",
    invite: list[str] | None = None,
    team_id: str = "",
    is_private: bool = False,
    status: SessionStatus = "active",
    items: list[dict[str, Any]] | None = None,
    summary_message_ts: str = "",
    summary_thread_ts: str = "",
    resource: dict[str, Any] | None = None,
    commands: list[dict[str, Any]] | None = None,
    view_type: ViewType = "diff",
    view_key: str = "",
    view_id: str = "",
    name: str = "",
    content: str = "",
    file_path: str = "",
    blocks: list[dict[str, Any]] | None = None,
    suggestions: dict[str, list[dict[str, Any]]] | None = None,
    canvas_id: str = "",
    access_level: CanvasAccessLevel = "write",
    base_branch: str = "",
    head_branch: str = "",
    csp: dict[str, list[str]] | None = None,
    include_resolved: bool = False,
) -> dict[str, Any]:
    """Implement the `manage_code_channel` tool."""
    try:
        cfg = RunConfig.from_runtime()
        thread_id = cfg.thread_id
        if not thread_id:
            return {"success": False, "error": "Missing thread_id in config"}

        client = langgraph_client()
        active = await get_active_slack_thread(
            client, thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
        )
        if not active:
            return {"success": False, "error": "Current Slack location is unavailable"}
        channel_id = str(active.get("channel_id") or "")
        thread_ts = str(active.get("thread_ts") or "")

        if action == "create":
            try:
                metadata = thread_metadata(await client.threads.get(thread_id))
            except Exception:
                return {"success": False, "error": "Cannot verify thread credential scope"}
            if thread_is_private(metadata):
                return {
                    "success": False,
                    "error": "Private threads cannot be promoted to code channels",
                }
            if is_code_channel_session(thread_ts):
                return {"success": False, "error": "This session is already a code channel"}
            return await _create(
                client,
                thread_id,
                active,
                await _code_channel_title(client, thread_id, title),
                cfg.repo.model_dump() if cfg.repo else None,
                invite=invite or [],
                team_id=team_id,
                is_private=is_private,
            )

        if not is_code_channel_session(thread_ts):
            return {"success": False, "error": "This session is not in a code channel"}

        if action == "status":
            data = await set_session_status_result(channel_id, status)
            return _result(action, channel_id, data)
        if action == "rename":
            await rename_session(channel_id, title)
            return _result(action, channel_id, None)
        if action == "context":
            if items is None:
                return {"success": False, "error": "items is required"}
            await set_context_bar(channel_id, items)
            return _result(action, channel_id, None)
        if action == "summary":
            data = await set_summary_message(
                channel_id,
                summary_message_ts.strip(),
                thread_ts=summary_thread_ts.strip(),
            )
            return _result(action, channel_id, data)
        if action == "resource":
            if resource is None:
                return {"success": False, "error": "resource is required"}
            data = await set_agent_resource(channel_id, resource)
            return _result(action, channel_id, data)
        if action == "commands":
            if commands is None:
                return {"success": False, "error": "commands is required; use [] to clear"}
            data = await set_commands(channel_id, commands)
            return _result(action, channel_id, data)
        if action == "view":
            resolved_content = await resolve_view_content(content, file_path)
            if suggestions is not None:
                if view_type != "block_kit":
                    return {
                        "success": False,
                        "error": "suggestions are only supported for block_kit views",
                    }
                suggestions_error = block_suggestions_error(suggestions)
                if suggestions_error:
                    return {"success": False, "error": suggestions_error}
            data = await set_view(
                channel_id,
                view_type,
                view_key=view_key,
                content=resolved_content,
                blocks=blocks,
                canvas_id=canvas_id,
                access_level=access_level,
                base_branch=base_branch,
                head_branch=head_branch,
                name=name or title,
                csp=csp,
            )
            result = _result(action, channel_id, data)
            if suggestions is not None and data:
                slack_view_id = data.get("view_id")
                if isinstance(slack_view_id, str) and slack_view_id:
                    try:
                        await store_block_suggestions(
                            client, channel_id, slack_view_id, suggestions
                        )
                    except Exception as exc:  # noqa: BLE001
                        result["warnings"] = [f"Could not store Block Kit suggestions: {exc}"]
            return result
        if action == "list_views":
            views = await list_views(channel_id)
            return _result(action, channel_id, {"views": views})
        if action == "remove_view":
            data = await remove_view(channel_id, view_key=view_key, view_id=view_id)
            result = _result(action, channel_id, data)
            removed_view_id = data.get("view_id") if data else None
            if isinstance(removed_view_id, str) and removed_view_id:
                with suppress(Exception):
                    await delete_block_suggestions(client, channel_id, removed_view_id)
            return result
        if action == "get_canvas":
            data = await get_canvas(channel_id, canvas_id, include_resolved=include_resolved)
            return _result(action, channel_id, data)
        if action == "set_canvas":
            resolved_content = await resolve_view_content(content, file_path)
            data = await set_canvas_content(channel_id, canvas_id, resolved_content)
            return _result(action, channel_id, data)
        if action == "archive":
            warning = ""
            try:
                await set_session_status_result(channel_id, "closed")
            except SlackRequestError as exc:
                warning = f"Could not set session status to closed: {exc.code}"
            await archive_code_channel(channel_id, summary_message_ts=summary_message_ts.strip())
            result = _result(action, channel_id, None)
            if warning:
                result["warnings"] = [warning]
            return result
        return {"success": False, "error": f"Unknown action {action}"}

    except (SlackRequestError, ValueError) as exc:
        return {"success": False, "error": str(exc)}


async def _code_channel_title(client: Any, thread_id: str, fallback: str) -> str:
    try:
        thread = await client.threads.get(thread_id=thread_id)
    except Exception:  # noqa: BLE001
        return fallback
    # Only trust metadata written by title generation: a missing title_seed key
    # (e.g. legacy title-only metadata) has no proof of a generated title.
    metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    if (
        not isinstance(metadata, Mapping)
        or "title_seed" not in metadata
        or metadata["title_seed"] is not None
    ):
        return fallback
    title = metadata.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else fallback


def _result(
    action: str,
    channel_id: str,
    data: dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"success": True, "action": action, "channel_id": channel_id}
    if data:
        result["data"] = data
    return result


async def resolve_view_content(content: str, file_path: str) -> str:
    if content and file_path:
        raise ValueError("Pass content or file_path, not both")
    if not file_path:
        return content
    try:
        backend, path, _ = await resolve_sandbox_file(file_path)
        downloads = await backend.adownload_files([path])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not read file_path: {exc}") from exc
    if not downloads or not downloads[0].content:
        raise ValueError("file_path is empty or unreadable")
    raw = downloads[0].content
    if len(raw) > VIEW_CONTENT_MAX_BYTES:
        raise ValueError("file_path exceeds Slack's 1 MB view limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file_path must contain valid UTF-8 text") from exc


async def _create(
    client: Any,
    thread_id: str,
    active: dict[str, Any],
    title: str,
    repo: dict[str, Any] | None,
    *,
    invite: list[str],
    team_id: str = "",
    is_private: bool = False,
) -> dict[str, Any]:
    if not title.strip():
        return {"success": False, "error": "title is required"}
    source_channel = str(active.get("channel_id") or "")
    source_ts = str(active.get("thread_ts") or "")
    origin_message_ts = str(active.get("triggering_event_ts") or "") or source_ts

    channel_id = await create_code_channel(
        name=title,
        session_id=thread_id,
        origin_channel_id=source_channel,
        origin_message_ts=origin_message_ts,
        team_id=team_id,
        is_private=is_private,
    )

    new_slack = SlackThreadRef.model_validate(
        {
            **{
                key: active.get(key, "")
                for key in (
                    "triggering_user_id",
                    "triggering_user_name",
                    "triggering_user_email",
                    "team_id",
                    "triggering_bot_id",
                    "triggering_bot_app_id",
                )
            },
            "channel_id": channel_id,
            "thread_ts": CODE_CHANNEL_SESSION_TS,
            "triggering_event_ts": origin_message_ts,
        }
    )
    try:
        await rebind_slack_thread(
            client, thread_id, SlackThreadRef.model_validate(active), new_slack
        )
    except SlackRebindError as exc:
        if exc.moved:
            return {
                "success": False,
                "error": f"Code channel created but the source thread was not detached: {exc}",
                "channel_id": channel_id,
                "retryable": True,
            }
        with suppress(Exception):
            await archive_code_channel(channel_id)
        return {
            "success": False,
            "error": f"Could not bind the code channel to this session: {exc}",
            "retryable": True,
        }

    warnings: list[str] = []
    invited: list[str] = []
    invitees = slack_user_ids(invite)
    if invitees:
        try:
            invited = await invite_to_slack_channel(channel_id, invitees)
        except SlackRequestError as exc:
            invited = exc.invited
            warnings.append(f"Could not invite {exc.code}")
    try:
        await set_session_status_result(channel_id, "processing")
    except SlackRequestError as exc:
        warnings.append(f"Could not set processing status: {exc.code}")
    context_items = repo_context_bar_items(
        repo, dashboard_url=dashboard_thread_url(thread_id) or ""
    )
    if context_items:
        try:
            await set_context_bar(channel_id, context_items)
        except SlackRequestError as exc:
            warnings.append(f"Could not set repository context: {exc.code}")
    try:
        await set_commands(channel_id, DEFAULT_CODE_CHANNEL_COMMANDS)
    except SlackRequestError as exc:
        warnings.append(f"Could not register default commands: {exc.code}")

    result: dict[str, Any] = {
        "success": True,
        "action": "create",
        "channel_id": channel_id,
        "dashboard_url": dashboard_thread_url(thread_id),
        "invited": invited,
    }
    if warnings:
        result["warnings"] = warnings
    return result
