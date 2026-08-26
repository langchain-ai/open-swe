"""Slack code channels, keyed with a non-message timestamp because the whole
channel is one agent session rather than a Slack thread.
"""

import logging
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

import httpx
from langgraph_sdk.client import LangGraphClient

from .http import DEFAULT_HTTP_TIMEOUT
from .slack import (
    SLACK_API_BASE_URL,
    SLACK_BOT_TOKEN,
    _slack_headers,
    get_slack_channel_info,
)

logger = logging.getLogger(__name__)

CODE_CHANNEL_SESSION_TS = "0"
VIEW_CONTENT_MAX_BYTES = 1_000_000
VIEW_CONTENT_MAX_CHARS = VIEW_CONTENT_MAX_BYTES

SessionStatus = Literal["processing", "active", "suspended", "closed"]
ViewType = Literal["html", "diff", "block_kit", "canvas"]
CanvasAccessLevel = Literal["read", "write", "comment"]

DEFAULT_CODE_CHANNEL_COMMANDS: list[dict[str, str]] = [
    {
        "name": "create-pr",
        "description": "Open a pull request for the current branch",
        "argument_hint": "[title]",
    },
    {
        "name": "run-tests",
        "description": "Run the relevant test suite and report the result",
        "argument_hint": "[test target]",
    },
    {
        "name": "summarize",
        "description": "Summarize completed work and remaining tasks",
    },
]

_CONTEXT_ICONS = frozenset(
    {
        "branch",
        "folder",
        "hierarchy",
        "life-ring",
        "link",
        "globe",
        "terminal",
        "code",
        "search",
        "lock",
    }
)
_VIEW_SUGGESTIONS_NAMESPACE = "slack_code_channel_view_suggestions"


def is_code_channel_session(thread_ts: str | None) -> bool:
    """Return whether a Slack location refers to a code channel session."""
    return thread_ts == CODE_CHANNEL_SESSION_TS


async def is_code_channel(channel_id: str) -> bool:
    """Return whether a Slack channel is a code channel owned by an agent."""
    channel = await get_slack_channel_info(channel_id)
    properties = channel.get("properties") if isinstance(channel, dict) else None
    record = properties.get("record_channel") if isinstance(properties, dict) else None
    return isinstance(record, dict) and record.get("record_type") == "agent_channel"


async def _call(method: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not SLACK_BOT_TOKEN:
        return None, "missing_slack_bot_token"
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/{method}",
                headers=_slack_headers(),
                json=payload,
            )
            if response.status_code == 429:
                return None, "rate_limited"
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("Slack %s request failed", method)
            return None, f"http_error: {type(exc).__name__}"
        if not isinstance(data, dict):
            return None, "invalid_response"
        if not data.get("ok"):
            error = str(data.get("error") or "unknown_error")
            logger.warning("Slack %s failed: %s", method, error)
            return None, error
        return data, None


async def _call_canvas_method(
    method: str, payload: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    data, error = await _call(method, payload)
    if error not in {"invalid_arguments", "missing_required_arg"}:
        return data, error
    legacy_payload = dict(payload)
    legacy_payload["channel"] = legacy_payload.pop("channel_id")
    return await _call(method, legacy_payload)


def _content_error(content: str) -> str | None:
    if not content:
        return "content_required"
    if len(content.encode("utf-8")) > VIEW_CONTENT_MAX_BYTES:
        return "content_too_large"
    return None


def _context_items_error(items: list[dict[str, Any]]) -> str | None:
    if len(items) > 5:
        return "too_many_context_bar_items"
    for item in items:
        key = item.get("key")
        label = item.get("label")
        icon = item.get("icon")
        url = item.get("url")
        item_type = item.get("item_type", "info")
        if not isinstance(key, str) or not 1 <= len(key) <= 64:
            return "invalid_context_bar_item_key"
        if not isinstance(label, str) or not 1 <= len(label) <= 128:
            return "invalid_context_bar_item_label"
        if icon is not None and icon not in _CONTEXT_ICONS:
            return "invalid_context_bar_item_icon"
        if url is not None and (not isinstance(url, str) or len(url) > 2048):
            return "invalid_context_bar_item_url"
        if item_type not in {"info", "action"}:
            return "invalid_context_bar_item_type"
        if item_type == "action" and url:
            return "context_bar_action_cannot_have_url"
    return None


async def create_code_channel(
    *,
    name: str,
    session_id: str,
    origin_channel_id: str,
    origin_message_ts: str,
    team_id: str = "",
    is_private: bool | None = None,
) -> tuple[str | None, str | None]:
    """Create a code channel for a task and return its channel id."""
    if not 1 <= len(name.strip()) <= 200:
        return None, "invalid_name"
    if not session_id or len(session_id) > 64:
        return None, "invalid_session_id"
    payload: dict[str, Any] = {
        "name": name.strip(),
        "session_id": session_id,
        "origin_channel_id": origin_channel_id,
        "origin_message_ts": origin_message_ts,
    }
    if team_id:
        payload["team_id"] = team_id
    if is_private is not None:
        payload["is_private"] = is_private
    data, error = await _call("agents.conversations.create", payload)
    if error or data is None:
        return None, error
    channel = data.get("channel")
    channel_id = channel.get("id") if isinstance(channel, dict) else data.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return channel_id, None
    return None, "missing_channel_id"


async def set_session_status_result(
    channel_id: str, status: SessionStatus
) -> tuple[dict[str, Any] | None, str | None]:
    if status not in {"processing", "active", "suspended", "closed"}:
        return None, "invalid_status"
    return await _call("agents.sessions.setStatus", {"channel_id": channel_id, "status": status})


async def set_session_status(channel_id: str, status: SessionStatus) -> bool:
    """Set a code channel's lifecycle status."""
    _, error = await set_session_status_result(channel_id, status)
    return error is None


async def rename_session(channel_id: str, title: str) -> tuple[bool, str | None]:
    """Rename a code channel session."""
    clean_title = title.strip()
    if not 1 <= len(clean_title) <= 200:
        return False, "invalid_title"
    _, error = await _call(
        "agents.sessions.rename",
        {"channel_id": channel_id, "title": clean_title},
    )
    return error is None, error


async def set_properties(
    channel_id: str,
    *,
    code_channel: dict[str, Any] | None = None,
    agent_resource: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if code_channel is None and agent_resource is None:
        return None, "no_properties_provided"
    payload: dict[str, Any] = {"channel_id": channel_id}
    if code_channel is not None:
        items = code_channel.get("context_bar_items")
        if items is not None:
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                return None, "invalid_context_bar_items"
            if error := _context_items_error(items):
                return None, error
        summary = code_channel.get("summary_message")
        if summary is not None and (
            not isinstance(summary, dict)
            or not isinstance(summary.get("message_ts"), str)
            or not summary["message_ts"]
        ):
            return None, "invalid_summary_message"
        payload["code_channel"] = code_channel
    if agent_resource is not None:
        if not agent_resource:
            return None, "invalid_agent_resource"
        limits = {"url": 2048, "resource_type": 64, "title": 255, "provider": 64}
        if any(
            key not in limits or not isinstance(value, str) or len(value) > limits[key]
            for key, value in agent_resource.items()
        ):
            return None, "invalid_agent_resource"
        payload["agent_resource"] = agent_resource
    return await _call("agents.conversations.setProperties", payload)


async def set_context_bar(channel_id: str, items: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Replace the agent-supplied items at the top of a code channel."""
    _, error = await set_properties(channel_id, code_channel={"context_bar_items": items})
    return error is None, error


async def set_summary_message(
    channel_id: str, message_ts: str, *, thread_ts: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    summary: dict[str, str] = {"message_ts": message_ts}
    if thread_ts:
        summary["thread_ts"] = thread_ts
    return await set_properties(channel_id, code_channel={"summary_message": summary})


async def set_agent_resource(
    channel_id: str, resource: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    return await set_properties(channel_id, agent_resource=resource)


async def set_commands(
    channel_id: str, commands: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    if len(commands) > 10:
        return None, "too_many_commands"
    names: set[str] = set()
    for command in commands:
        name = command.get("name")
        description = command.get("description")
        hint = command.get("argument_hint")
        should_escape = command.get("should_escape")
        if (
            not isinstance(name, str)
            or name.startswith("/")
            or not 1 <= len(name) <= 31
            or not isinstance(description, str)
            or not 1 <= len(description) <= 100
            or (hint is not None and (not isinstance(hint, str) or len(hint) > 50))
            or (should_escape is not None and not isinstance(should_escape, bool))
        ):
            return None, "invalid_commands"
        if name in names:
            return None, "duplicate_command"
        names.add(name)
    return await _call(
        "agents.conversations.setCommands",
        {"channel_id": channel_id, "commands": commands},
    )


def block_suggestions_error(suggestions: dict[str, list[dict[str, Any]]]) -> str | None:
    for action_id, options in suggestions.items():
        if not action_id or len(action_id) > 255 or len(options) > 100:
            return "invalid_block_suggestions"
        for option in options:
            text = option.get("text")
            if (
                not isinstance(text, dict)
                or text.get("type") != "plain_text"
                or not isinstance(text.get("text"), str)
                or not text["text"]
                or len(text["text"]) > 75
                or not isinstance(option.get("value"), str)
                or not option["value"]
                or len(option["value"]) > 150
            ):
                return "invalid_block_suggestions"
    return None


async def store_block_suggestions(
    client: LangGraphClient,
    channel_id: str,
    view_id: str,
    suggestions: dict[str, list[dict[str, Any]]],
) -> None:
    if error := block_suggestions_error(suggestions):
        raise ValueError(error)
    await client.store.put_item(
        (_VIEW_SUGGESTIONS_NAMESPACE, channel_id, view_id),
        "suggestions",
        {"actions": suggestions},
    )


async def get_block_suggestions(
    client: LangGraphClient,
    channel_id: str,
    view_id: str,
    action_id: str,
    query: str,
) -> list[dict[str, Any]]:
    item = await client.store.get_item(
        (_VIEW_SUGGESTIONS_NAMESPACE, channel_id, view_id), "suggestions"
    )
    value = item.get("value") if isinstance(item, Mapping) else None
    actions = value.get("actions") if isinstance(value, dict) else None
    options = actions.get(action_id) if isinstance(actions, dict) else None
    if not isinstance(options, list):
        return []
    normalized_query = query.casefold().strip()
    return [
        option
        for option in options
        if isinstance(option, dict)
        and (
            not normalized_query
            or normalized_query
            in str(
                option.get("text", {}).get("text", "")
                if isinstance(option.get("text"), dict)
                else ""
            ).casefold()
        )
    ][:100]


async def delete_block_suggestions(client: LangGraphClient, channel_id: str, view_id: str) -> None:
    await client.store.delete_item(
        (_VIEW_SUGGESTIONS_NAMESPACE, channel_id, view_id), "suggestions"
    )


async def set_view(
    channel_id: str,
    view_type: ViewType,
    *,
    view_key: str = "",
    content: str = "",
    blocks: list[dict[str, Any]] | None = None,
    canvas_id: str = "",
    access_level: CanvasAccessLevel = "write",
    base_branch: str = "",
    head_branch: str = "",
    name: str = "",
    csp: dict[str, list[str]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if view_type not in {"html", "diff", "block_kit", "canvas"}:
        return None, "invalid_view_type"
    if view_type != "diff" and not 1 <= len(view_key) <= 256:
        return None, "invalid_view_key"
    if name and len(name) > 256:
        return None, "invalid_name"

    payload: dict[str, Any] = {"channel_id": channel_id, "type": view_type}
    if view_type in {"html", "diff"}:
        if error := _content_error(content):
            return None, error
        payload["content"] = content
    elif view_type == "block_kit":
        if not blocks or not all(isinstance(block, dict) for block in blocks):
            return None, "invalid_blocks"
        payload["blocks"] = blocks
    else:
        if not canvas_id:
            return None, "canvas_id_required"
        if access_level not in {"read", "write", "comment"}:
            return None, "invalid_access_level"
        payload.update({"canvas_id": canvas_id, "access_level": access_level})

    if view_type != "diff":
        payload["view_key"] = view_key
    if name:
        payload["name"] = name
    if view_type == "diff":
        if len(base_branch) > 255 or len(head_branch) > 255:
            return None, "invalid_branch_name"
        if base_branch:
            payload["base_branch"] = base_branch
        if head_branch:
            payload["head_branch"] = head_branch
    if view_type == "html" and csp:
        if any(
            key not in {"resource_domains", "connect_domains"}
            or not isinstance(domains, list)
            or len(domains) > 20
            or not all(isinstance(domain, str) for domain in domains)
            for key, domains in csp.items()
        ):
            return None, "invalid_csp_domain"
        payload["csp"] = csp
    return await _call("agents.conversations.setView", payload)


async def set_diff_view(
    channel_id: str,
    content: str,
    *,
    base_branch: str = "",
    head_branch: str = "",
) -> tuple[bool, str | None]:
    """Create or replace the singleton diff tab on a code channel."""
    _, error = await set_view(
        channel_id,
        "diff",
        content=content,
        base_branch=base_branch,
        head_branch=head_branch,
    )
    return error is None, error


async def list_views(
    channel_id: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    data, error = await _call("agents.conversations.listViews", {"channel_id": channel_id})
    if error or data is None:
        return None, error
    views = data.get("views")
    if not isinstance(views, list) or not all(isinstance(view, dict) for view in views):
        return None, "invalid_views_response"
    return views, None


async def remove_view(
    channel_id: str, *, view_key: str = "", view_id: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    if bool(view_key) == bool(view_id):
        return None, "invalid_arguments"
    payload = {"channel_id": channel_id}
    payload["view_key" if view_key else "view_id"] = view_key or view_id
    return await _call("agents.conversations.removeView", payload)


async def get_canvas(
    channel_id: str, canvas_id: str, *, include_resolved: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    if not canvas_id:
        return None, "canvas_id_required"
    return await _call_canvas_method(
        "agents.conversations.getCanvas",
        {
            "channel_id": channel_id,
            "canvas_id": canvas_id,
            "content_format": "markdown",
            "include_resolved": include_resolved,
        },
    )


async def set_canvas_content(
    channel_id: str, canvas_id: str, content: str
) -> tuple[dict[str, Any] | None, str | None]:
    if not canvas_id:
        return None, "canvas_id_required"
    if error := _content_error(content):
        return None, error
    return await _call_canvas_method(
        "agents.conversations.setCanvasContent",
        {"channel_id": channel_id, "canvas_id": canvas_id, "content": content},
    )


async def archive_code_channel(
    channel_id: str, *, summary_message_ts: str = ""
) -> tuple[bool, str | None]:
    """Archive a code channel, recording the agent's closing summary."""
    payload: dict[str, Any] = {"channel_id": channel_id}
    if summary_message_ts:
        payload["summary_message_ts"] = summary_message_ts
    _, error = await _call("agents.conversations.archive", payload)
    return error is None, error


def repo_context_bar_items(
    repo: dict[str, str] | None, *, branch: str = "", pr_url: str = ""
) -> list[dict[str, Any]]:
    """Build the standard repo/branch/PR context bar for an Open SWE session."""
    items: list[dict[str, Any]] = []
    owner = (repo or {}).get("owner", "")
    name = (repo or {}).get("name", "")
    if owner and name:
        items.append(
            {
                "key": "repo",
                "label": f"{owner}/{name}",
                "icon": "folder",
                "url": f"https://github.com/{owner}/{name}",
            }
        )
    if branch:
        item = {"key": "branch", "label": branch, "icon": "branch"}
        if owner and name:
            item["url"] = f"https://github.com/{owner}/{name}/tree/{quote(branch, safe='/')}"
        items.append(item)
    if pr_url.startswith("https://"):
        items.append({"key": "pr", "label": "Pull request", "icon": "hierarchy", "url": pr_url})
    return items
