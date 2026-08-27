from collections.abc import Mapping
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
from ..utils.thread_ops import langgraph_client
from .create_sandbox_file_download_url import _resolve_sandbox_file


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
    team_id: str = "",
    is_private: bool | None = None,
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
    """Manage the complete Slack code-channel surface for this session.

    Use `create` to promote the current Slack thread using its generated title. Use
    `status`, `rename`, `context`, `summary`, `resource`, and `commands` for channel chrome. `view`
    upserts an `html`, `diff`, `block_kit`, or `canvas` tab; HTML and diff content
    can be passed directly or read from `file_path`, while Block Kit uses `blocks`
    plus optional external-select `suggestions`, and canvas uses `canvas_id`. Use
    `list_views` and `remove_view` to reconcile
    tabs. Use `get_canvas` to read markdown and comments and `set_canvas` to
    replace its markdown while preserving comment anchors. Post a closing summary
    before `archive` and pass its timestamp as `summary_message_ts`.

    Files must be inside the active sandbox work directory, valid UTF-8, and at
    most 1 MB. Never publish secrets or credentials in a view.
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
        repo = configurable.get("repo")
        return await _create(
            client,
            thread_id,
            active,
            await _code_channel_title(client, thread_id, title),
            repo if isinstance(repo, dict) else None,
            team_id=team_id,
            is_private=is_private,
        )

    if not is_code_channel_session(thread_ts):
        return {"success": False, "error": "This session is not in a code channel"}

    if action == "status":
        data, error = await set_session_status_result(channel_id, status)
        return _result(action, channel_id, data, error)
    if action == "rename":
        ok, error = await rename_session(channel_id, title)
        return _result(action, channel_id, None, error if not ok else None)
    if action == "context":
        if items is None:
            return {"success": False, "error": "items is required"}
        ok, error = await set_context_bar(channel_id, items)
        return _result(action, channel_id, None, error if not ok else None)
    if action == "summary":
        data, error = await set_summary_message(
            channel_id,
            summary_message_ts.strip(),
            thread_ts=summary_thread_ts.strip(),
        )
        return _result(action, channel_id, data, error)
    if action == "resource":
        if resource is None:
            return {"success": False, "error": "resource is required"}
        data, error = await set_agent_resource(channel_id, resource)
        return _result(action, channel_id, data, error)
    if action == "commands":
        if commands is None:
            return {"success": False, "error": "commands is required; use [] to clear"}
        data, error = await set_commands(channel_id, commands)
        return _result(action, channel_id, data, error)
    if action == "view":
        resolved_content, content_error = await _resolve_content(content, file_path)
        if content_error:
            return {"success": False, "error": content_error}
        if suggestions is not None:
            if view_type != "block_kit":
                return {
                    "success": False,
                    "error": "suggestions are only supported for block_kit views",
                }
            suggestions_error = block_suggestions_error(suggestions)
            if suggestions_error:
                return {"success": False, "error": suggestions_error}
        data, error = await set_view(
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
        result = _result(action, channel_id, data, error)
        if not error and suggestions is not None and data:
            slack_view_id = data.get("view_id")
            if isinstance(slack_view_id, str) and slack_view_id:
                try:
                    await store_block_suggestions(client, channel_id, slack_view_id, suggestions)
                except Exception as exc:  # noqa: BLE001
                    result["warnings"] = [f"Could not store Block Kit suggestions: {exc}"]
        return result
    if action == "list_views":
        views, error = await list_views(channel_id)
        return _result(action, channel_id, {"views": views} if views is not None else None, error)
    if action == "remove_view":
        data, error = await remove_view(channel_id, view_key=view_key, view_id=view_id)
        result = _result(action, channel_id, data, error)
        removed_view_id = data.get("view_id") if data else None
        if not error and isinstance(removed_view_id, str) and removed_view_id:
            with suppress(Exception):
                await delete_block_suggestions(client, channel_id, removed_view_id)
        return result
    if action == "get_canvas":
        data, error = await get_canvas(channel_id, canvas_id, include_resolved=include_resolved)
        return _result(action, channel_id, data, error)
    if action == "set_canvas":
        resolved_content, content_error = await _resolve_content(content, file_path)
        if content_error:
            return {"success": False, "error": content_error}
        data, error = await set_canvas_content(channel_id, canvas_id, resolved_content)
        return _result(action, channel_id, data, error)
    if action == "archive":
        _, status_error = await set_session_status_result(channel_id, "closed")
        ok, error = await archive_code_channel(
            channel_id, summary_message_ts=summary_message_ts.strip()
        )
        result = _result(action, channel_id, None, error if not ok else None)
        if status_error:
            result["warnings"] = [f"Could not set session status to closed: {status_error}"]
        return result
    return {"success": False, "error": f"Unknown action {action}"}


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
    error: str | None,
) -> dict[str, Any]:
    if error:
        return {"success": False, "error": error}
    result: dict[str, Any] = {"success": True, "action": action, "channel_id": channel_id}
    if data:
        result["data"] = data
    return result


async def _resolve_content(content: str, file_path: str) -> tuple[str, str | None]:
    if content and file_path:
        return "", "Pass content or file_path, not both"
    if not file_path:
        return content, None
    try:
        backend, path, _ = await _resolve_sandbox_file(file_path)
        downloads = await backend.adownload_files([path])
    except Exception as exc:  # noqa: BLE001
        return "", f"Could not read file_path: {exc}"
    if not downloads or not downloads[0].content:
        return "", "file_path is empty or unreadable"
    raw = downloads[0].content
    if len(raw) > VIEW_CONTENT_MAX_BYTES:
        return "", "file_path exceeds Slack's 1 MB view limit"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return "", "file_path must contain valid UTF-8 text"


async def _create(
    client: Any,
    thread_id: str,
    active: dict[str, Any],
    title: str,
    repo: dict[str, Any] | None,
    *,
    team_id: str = "",
    is_private: bool | None = None,
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
        team_id=team_id,
        is_private=is_private,
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

    warnings: list[str] = []
    _, status_error = await set_session_status_result(channel_id, "processing")
    if status_error:
        warnings.append(f"Could not set processing status: {status_error}")
    context_items = repo_context_bar_items(
        repo, dashboard_url=dashboard_thread_url(thread_id) or ""
    )
    if context_items:
        _, context_error = await set_context_bar(channel_id, context_items)
        if context_error:
            warnings.append(f"Could not set repository context: {context_error}")
    _, commands_error = await set_commands(channel_id, DEFAULT_CODE_CHANNEL_COMMANDS)
    if commands_error:
        warnings.append(f"Could not register default commands: {commands_error}")

    result: dict[str, Any] = {
        "success": True,
        "action": "create",
        "channel_id": channel_id,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
    if warnings:
        result["warnings"] = warnings
    return result
