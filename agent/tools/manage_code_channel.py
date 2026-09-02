import logging
import shlex
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.config import get_config

from agent.sandboxes.paths import resolve_repo_dir
from agent.sandboxes.state import get_sandbox_backend
from agent.source_context import SourceContext
from agent.spawn import CodeChannelError, SpawnOrigin, open_code_channel

from ..utils.dashboard_links import dashboard_thread_url
from ..utils.slack import (
    get_active_slack_thread,
    get_slack_permalink,
    slack_user_ids,
)
from ..utils.slack_code_channels import (
    VIEW_CONTENT_MAX_BYTES,
    CanvasAccessLevel,
    SessionStatus,
    ViewType,
    archive_code_channel,
    block_suggestions_error,
    delete_block_suggestions,
    get_canvas,
    is_code_channel_session,
    list_views,
    remove_view,
    rename_session,
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
    instructions: str = "",
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
    """Manage the complete Slack code-channel surface for this session.

    `create` opens a code channel and starts a **separate session** in it, which
    takes the task over from here. That session begins with no history and its own
    fresh sandbox, so `instructions` must describe the task on its own: what to do,
    what has been decided, and anything learned so far that it would otherwise have
    to rediscover. Push any local work before calling it — commit and push to a
    branch, creating one if the work is still on the default branch — because the
    new sandbox is a clean checkout and cannot see this one's working tree; `create`
    refuses while this checkout holds unpushed work. After it succeeds, tell the
    user which channel is handling the task and stop working on it here.

    `invite` is who starts out in that channel, as Slack user ids, and it needs at
    least one person — a channel nobody is in is a channel nobody reads. Include
    whoever asked for the work, plus anyone they named or anyone already taking
    part in this conversation. User ids appear in the conversation context (e.g.
    @Name(U06KD8BFY95)).

    Use `status`, `rename`, `context`, `summary`, `resource`, and `commands` for channel chrome. `view`
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
            configurable=dict(configurable),
            instructions=instructions,
            invite=invite or [],
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


@dataclass(frozen=True)
class _OriginRepoState:
    """What the origin sandbox holds that a fresh sandbox would not."""

    branch: str = ""
    unshared: str = ""


async def _origin_repo_state(thread_id: str, repo: dict[str, Any] | None) -> _OriginRepoState:
    """The origin checkout's branch, and anything in it that GitHub has not seen.

    Best effort: a sandbox that cannot answer is not a reason to refuse the
    channel, so an unreachable sandbox or a missing checkout reads as clean.
    """
    repo_name = str((repo or {}).get("name") or "")
    if not repo_name:
        return _OriginRepoState()
    try:
        backend = await get_sandbox_backend(thread_id)
        repo_dir = shlex.quote(await resolve_repo_dir(backend, repo_name))
        branch = await backend.aexecute(f"git -C {repo_dir} rev-parse --abbrev-ref HEAD")
        dirty = await backend.aexecute(f"git -C {repo_dir} status --porcelain")
        # Commits on no remote branch: committed to a local branch and never pushed.
        unpushed = await backend.aexecute(
            f"git -C {repo_dir} log --branches --not --remotes --format=%h"
        )
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the origin checkout for %s", thread_id, exc_info=True)
        return _OriginRepoState()
    reasons: list[str] = []
    if dirty.exit_code == 0 and dirty.output.strip():
        reasons.append("uncommitted changes")
    if unpushed.exit_code == 0 and unpushed.output.strip():
        reasons.append("commits that were never pushed")
    return _OriginRepoState(
        branch=branch.output.strip() if branch.exit_code == 0 else "",
        unshared=" and ".join(reasons),
    )


def _handoff_repo(repo: dict[str, Any] | None) -> dict[str, str] | None:
    owner = str((repo or {}).get("owner") or "")
    name = str((repo or {}).get("name") or "")
    return {"owner": owner, "name": name} if owner and name else None


def _pull_request_url(metadata: Mapping[str, Any] | None) -> str:
    urls = (metadata or {}).get("pr_urls")
    if not isinstance(urls, list):
        return ""
    return next(
        (url for url in reversed(urls) if isinstance(url, str) and url.startswith("https://")), ""
    )


async def _handoff_content(
    *,
    title: str,
    instructions: str,
    repo: dict[str, Any] | None,
    repo_state: _OriginRepoState,
    pull_request_url: str,
    origin: Mapping[str, Any],
    origin_thread_id: str,
) -> str:
    owner = str((repo or {}).get("owner") or "")
    name = str((repo or {}).get("name") or "")
    repo_text = f"{owner}/{name}" if owner and name else "(no repository specified)"
    started_by = str(origin.get("triggering_user_name") or "") or (
        f"<@{origin.get('triggering_user_id')}>" if origin.get("triggering_user_id") else "unknown"
    )
    permalink = await get_slack_permalink(
        str(origin.get("channel_id") or ""),
        str(origin.get("triggering_event_ts") or origin.get("thread_ts") or ""),
    )

    checkout_lines = [
        f"- Branch to continue from: `{repo_state.branch}`"
        if repo_state.branch
        else "- No branch was started yet.",
        "- This session has its own fresh sandbox. The originating session's working "
        "tree is not shared with it, so everything you need is on the branch above, "
        "in the pull request, or in the task description.",
    ]
    if pull_request_url:
        checkout_lines.insert(1, f"- Pull request: {pull_request_url}")

    origin_lines = [f"- Started by {started_by}"]
    if permalink:
        origin_lines.append(f"- Originating Slack thread: {permalink}")
    origin_dashboard = dashboard_thread_url(origin_thread_id)
    if origin_dashboard:
        origin_lines.append(f"- Originating session: {origin_dashboard}")
    origin_lines.append(
        "- That session handed this task over and is no longer working on it. Do not "
        "post there; this channel is where the work and the conversation happen."
    )

    return "\n\n".join(
        section
        for section in (
            "You are the agent for a new Slack code channel, opened for this task by "
            "another Open SWE session. The whole channel is one session with you.",
            f"## Task\n{title}",
            f"## Instructions\n{instructions}",
            f"## Default Repository Hint\n{repo_text}\n"
            "Use this repository unless the instructions clearly identify a different one.",
            "## Checkout\n" + "\n".join(checkout_lines),
            "## Origin\n" + "\n".join(origin_lines),
        )
        if section
    )


async def _record_spawn_on_origin(
    client: Any, origin_thread_id: str, channel_id: str, session_thread_id: str
) -> None:
    """Append to the channels this thread has handed tasks to.

    A thread can open as many code channels as it has tasks — each one is its own
    disconnected session — so this is a list, not the latest one.
    """
    try:
        thread = await client.threads.get(thread_id=origin_thread_id)
        metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
        context = SourceContext.from_metadata(metadata).dump()
        recorded = context.get("spawned_code_channels")
        recorded = (
            [entry for entry in recorded if isinstance(entry, dict)]
            if isinstance(recorded, list)
            else []
        )
        if not any(entry.get("channel_id") == channel_id for entry in recorded):
            recorded.append({"channel_id": channel_id, "thread_id": session_thread_id})
        merged = SourceContext.parse({**context, "spawned_code_channels": recorded})
        await client.threads.update(
            thread_id=origin_thread_id, metadata={"source_context": merged.dump()}
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not record the code channel on origin thread %s", origin_thread_id, exc_info=True
        )


async def _create(
    client: Any,
    thread_id: str,
    active: dict[str, Any],
    title: str,
    repo: dict[str, Any] | None,
    *,
    configurable: dict[str, Any],
    instructions: str,
    invite: list[str],
    team_id: str = "",
    is_private: bool = False,
) -> dict[str, Any]:
    if not title.strip():
        return {"success": False, "error": "title is required"}
    if not instructions.strip():
        return {
            "success": False,
            "error": (
                "instructions is required: the code channel session starts with no history, "
                "so it needs a self-contained description of the task"
            ),
        }
    # Falling back to whoever triggered this would put the wrong person in a
    # channel as easily as the right one, so the caller has to name them.
    invitees = slack_user_ids(invite)
    if not invitees:
        triggering_user = str(active.get("triggering_user_id") or "")
        return {
            "success": False,
            "error": (
                "invite is required: name the Slack user ids who should be in the channel"
                + (f", starting with <@{triggering_user}>" if triggering_user else "")
            ),
        }
    source_channel = str(active.get("channel_id") or "")
    origin_message_ts = str(active.get("triggering_event_ts") or "") or str(
        active.get("thread_ts") or ""
    )

    repo_state = await _origin_repo_state(thread_id, repo)
    if repo_state.unshared:
        return {
            "success": False,
            "error": (
                f"This session's checkout has {repo_state.unshared}. The code channel gets a "
                "fresh sandbox and cannot see this working tree, so that work would be lost. "
                "Push it first — create a branch for it if there is none yet — then open the "
                "channel."
            ),
        }

    try:
        thread = await client.threads.get(thread_id=thread_id)
        origin_metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    except Exception:  # noqa: BLE001
        origin_metadata = None

    try:
        opened = await open_code_channel(
            client,
            title=title,
            content=await _handoff_content(
                title=title,
                instructions=instructions,
                repo=repo,
                repo_state=repo_state,
                pull_request_url=_pull_request_url(origin_metadata),
                origin=active,
                origin_thread_id=thread_id,
            ),
            repo=_handoff_repo(repo),
            origin=SpawnOrigin.from_config({**configurable, "thread_id": thread_id}, active),
            invite=invitees,
            source_context={
                "spawned_from": {
                    "thread_id": thread_id,
                    "channel_id": source_channel,
                    "thread_ts": str(active.get("thread_ts") or ""),
                    "message_ts": origin_message_ts,
                }
            },
            origin_channel_id=source_channel,
            origin_message_ts=origin_message_ts,
            team_id=team_id,
            is_private=is_private,
        )
    except CodeChannelError as exc:
        failure: dict[str, Any] = {"success": False, "error": exc.message}
        if exc.retryable:
            failure["retryable"] = True
        return failure
    channel_id = opened.channel_id
    session = opened.session
    warnings = list(opened.warnings)

    await _record_spawn_on_origin(client, thread_id, channel_id, session.thread_id)

    result: dict[str, Any] = {
        "success": True,
        "action": "create",
        "channel_id": channel_id,
        "mention": f"<#{channel_id}>",
        "thread_id": session.thread_id,
        "invited": opened.invited,
        "dashboard_url": session.dashboard_url,
        "next_step": (
            "The channel is a separate session that has already started on the instructions "
            f"you gave it. Tell the user it is working in <#{channel_id}> and stop working on "
            "this task here."
        ),
    }
    if warnings:
        result["warnings"] = warnings
    return result
