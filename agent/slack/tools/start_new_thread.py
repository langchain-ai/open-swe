import asyncio
import re
import uuid
from typing import Any

from fastapi import HTTPException

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.run_config import RunConfig
from agent.slack.client import (
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
    post_slack_top_level_message_with_ts,
)
from agent.slack.spawn import SpawnDestination, SpawnHandoff, SpawnOrigin, spawn_slack_session
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.langsmith import get_langsmith_trace_url
from agent.utils.thread_ops import langgraph_client
from agent.webhooks.common import _is_repo_allowed

_TITLE_MAX_CHARS = 160
_INSTRUCTIONS_MAX_CHARS = 12000
_VISIBLE_INSTRUCTIONS_MAX_CHARS = 2800
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _failure_hint(slack_error: str | None) -> str:
    if slack_error == "msg_too_long":
        return "Slack rejected the message as too long; retry with shorter title or instructions."
    if slack_error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the channel; do not retry with another channel."
    if slack_error and slack_error.startswith("rate_limited"):
        retry_after = slack_error.partition(":")[2].strip()
        if retry_after:
            return f"Slack rate limited the request; wait at least {retry_after}s before retrying."
        return "Slack rate limited the request; wait before retrying."
    if slack_error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry."
    if slack_error and slack_error.startswith("http_error:"):
        return "Slack posting hit an HTTP error; retry once."
    return "Slack post failed; retry once with concise instructions."


def _rate_limit_delay(slack_error: str | None) -> float | None:
    if slack_error == "rate_limited":
        return 1
    prefix = "rate_limited: "
    if not slack_error or not slack_error.startswith(prefix):
        return None
    try:
        delay = float(slack_error.removeprefix(prefix))
    except ValueError:
        return None
    return delay if 0 <= delay <= 60 else None


def _validate_text(value: str, *, field: str, max_chars: int) -> str | dict[str, Any]:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return {"success": False, "error": f"{field} is required"}
    if len(text) > max_chars:
        return {
            "success": False,
            "error": f"{field} is too long",
            "max_chars": max_chars,
            "actual_chars": len(text),
        }
    return text


def _resolve_repo(cfg: RunConfig, default_repo: str | None) -> dict[str, str] | None:
    if default_repo and default_repo.strip():
        candidate = default_repo.strip()
        if not _REPO_RE.fullmatch(candidate):
            return None
        owner, name = candidate.split("/", 1)
        return {"owner": owner, "name": name}

    if cfg.repo and cfg.repo.owner.strip() and cfg.repo.name.strip():
        return {"owner": cfg.repo.owner.strip(), "name": cfg.repo.name.strip()}
    return None


def _truncate_for_slack(text: str) -> str:
    if len(text) <= _VISIBLE_INSTRUCTIONS_MAX_CHARS:
        return text
    omitted = len(text) - _VISIBLE_INSTRUCTIONS_MAX_CHARS
    return f"{text[:_VISIBLE_INSTRUCTIONS_MAX_CHARS].rstrip()}\n\n…truncated {omitted} chars; the new Open SWE thread received the full instructions."


def _visible_message(title: str) -> str:
    return f"*Open SWE breakout thread:* {title}"


def _thread_details(instructions: str, repo: dict[str, str] | None) -> str:
    repo_line = f"*Repository:* `{repo['owner']}/{repo['name']}`\n\n" if repo else ""
    return f"{repo_line}*Instructions for the new thread:*\n{_truncate_for_slack(instructions)}"


async def _run_links_section(thread_id: str) -> str:
    dashboard_url = dashboard_thread_url(thread_id)
    trace_url = await get_langsmith_trace_url(thread_id)
    lines = ["## Open SWE Links"]
    if dashboard_url:
        lines.append(f"- Web: {dashboard_url}")
    if trace_url:
        lines.append(f"- Trace: {trace_url}")
    lines.append(
        "- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked."
    )
    return "\n".join(lines)


async def _run_prompt(
    title: str,
    instructions: str,
    repo: dict[str, str] | None,
    original_slack_thread: dict[str, Any],
    thread_id: str,
) -> str:
    repo_text = f"{repo['owner']}/{repo['name']}" if repo else "(no repository specified)"
    channel_id = original_slack_thread.get("channel_id", "")
    thread_ts = original_slack_thread.get("thread_ts", "")
    return (
        "You were started from another Open SWE Slack thread as a breakout task.\n\n"
        f"## Breakout Title\n{title}\n\n"
        f"## Default Repository Hint\n{repo_text}\n"
        "Use this repository unless the instructions below clearly identify a different repository.\n\n"
        "## Source Slack Thread\n"
        f"- Channel: {channel_id}\n"
        f"- Thread TS: {thread_ts}\n"
        "- Thread version: 0\n\n"
        f"{await _run_links_section(thread_id)}\n\n"
        "## Breakout Instructions\n"
        f"{instructions}"
    )


async def slack_start_new_thread(
    title: str,
    instructions: str,
    default_repo: str | None = None,
) -> dict[str, Any]:
    """Start a Slack thread with a headline root and instructions as the first reply."""
    cfg = RunConfig.from_runtime()
    if cfg.slack_thread is None:
        return {"success": False, "error": "Missing slack_thread config"}
    client = langgraph_client()
    current_slack_thread = await get_active_slack_thread(
        client,
        cfg.thread_id,
        cfg.slack_thread.dump(),
    )
    if not current_slack_thread:
        return {"success": False, "error": "Current Slack location is unavailable"}

    channel_id = current_slack_thread.get("channel_id")
    current_thread_ts = current_slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return {"success": False, "error": "Missing slack_thread.channel_id in config"}

    clean_title = _validate_text(title, field="title", max_chars=_TITLE_MAX_CHARS)
    if isinstance(clean_title, dict):
        return clean_title
    clean_instructions = _validate_text(
        instructions, field="instructions", max_chars=_INSTRUCTIONS_MAX_CHARS
    )
    if isinstance(clean_instructions, dict):
        return clean_instructions

    repo = _resolve_repo(cfg, default_repo)
    if default_repo and default_repo.strip() and repo is None:
        return {
            "success": False,
            "error": "default_repo must be a simple owner/name repository string",
        }

    if default_repo and default_repo.strip() and repo is not None:
        if not _is_repo_allowed(repo):
            return {
                "success": False,
                "error": (
                    f"Repository {repo['owner']}/{repo['name']} is not on the deployment allowlist"
                ),
            }
        github_login = cfg.github_login
        if not (github_login or "").strip():
            return {
                "success": False,
                "error": (
                    "Cannot verify access to the requested repository: no github_login on the "
                    "parent thread"
                ),
            }
        try:
            await require_repo_access_for_user(
                (github_login or "").strip(), f"{repo['owner']}/{repo['name']}"
            )
        except HTTPException as exc:
            return {
                "success": False,
                "error": (
                    f"Access to repository {repo['owner']}/{repo['name']} denied: {exc.detail}"
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": (
                    f"Failed to verify access to repository {repo['owner']}/{repo['name']}: {exc}"
                ),
            }

    clean_channel_id = channel_id.strip()
    message_ts, slack_error = await post_slack_top_level_message_with_ts(
        clean_channel_id,
        _visible_message(clean_title),
        unfurl_links=False,
        unfurl_media=False,
    )
    if message_ts is None:
        return {
            "success": False,
            "error": slack_error or "post failed",
            "slack_error": slack_error,
            "hint": _failure_hint(slack_error),
        }

    details_ts: str | None = None
    details_error: str | None = None
    for attempt in range(2):
        details_ts, details_error = await post_slack_thread_reply_with_ts(
            clean_channel_id,
            message_ts,
            _thread_details(clean_instructions, repo),
            unfurl_links=False,
            unfurl_media=False,
        )
        if details_ts is not None:
            break
        delay = _rate_limit_delay(details_error)
        if attempt or delay is None:
            break
        await asyncio.sleep(delay)
    if details_ts is None:
        return {
            "success": False,
            "error": details_error or "thread details post failed",
            "slack_error": details_error,
            "hint": _failure_hint(details_error),
        }

    # The breakout's own links go in its first prompt, so it needs its thread id
    # before the session starts.
    thread_id = str(uuid.uuid4())
    session = await spawn_slack_session(
        client,
        destination=SpawnDestination(channel_id=clean_channel_id, thread_ts=message_ts),
        origin=SpawnOrigin.from_config(cfg, current_slack_thread),
        handoff=SpawnHandoff(
            title=clean_title,
            content=await _run_prompt(
                clean_title, clean_instructions, repo, current_slack_thread, thread_id
            ),
            repo=repo,
            source_context={
                "breakout_from": {
                    "channel_id": clean_channel_id,
                    "thread_ts": current_thread_ts or "",
                    "message_ts": current_slack_thread.get("triggering_event_ts", ""),
                }
            },
        ),
        thread_id=thread_id,
    )

    return {
        "success": True,
        "thread_id": session.thread_id,
        "thread_ts": message_ts,
        "dashboard_url": session.dashboard_url,
    }
