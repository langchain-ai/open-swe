import os
import re
from typing import Any

from fastapi import HTTPException
from langgraph.config import get_config
from langgraph_sdk import get_client

from ..dashboard.repo_access import require_repo_access_for_user
from ..dispatch import dispatch_agent_run
from ..utils.dashboard_links import dashboard_thread_url
from ..utils.langsmith import get_langsmith_trace_url
from ..utils.slack import post_slack_top_level_message_with_ts, store_slack_run_mapping
from ..utils.thread_ids import generate_thread_id_from_slack_thread
from ..webhooks.common import _is_repo_allowed

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

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


def _resolve_repo(configurable: dict[str, Any], default_repo: str | None) -> dict[str, str] | None:
    if default_repo and default_repo.strip():
        candidate = default_repo.strip()
        if not _REPO_RE.fullmatch(candidate):
            return None
        owner, name = candidate.split("/", 1)
        return {"owner": owner, "name": name}

    repo = configurable.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner.strip() and isinstance(name, str) and name.strip():
            return {"owner": owner.strip(), "name": name.strip()}
    return None


def _truncate_for_slack(text: str) -> str:
    if len(text) <= _VISIBLE_INSTRUCTIONS_MAX_CHARS:
        return text
    omitted = len(text) - _VISIBLE_INSTRUCTIONS_MAX_CHARS
    return f"{text[:_VISIBLE_INSTRUCTIONS_MAX_CHARS].rstrip()}\n\n…truncated {omitted} chars; the new Open SWE thread received the full instructions."


def _visible_message(title: str, instructions: str, repo: dict[str, str] | None) -> str:
    repo_line = f"\n*Repository:* `{repo['owner']}/{repo['name']}`" if repo else ""
    return (
        f"*Open SWE breakout thread:* {title}{repo_line}\n\n"
        f"*Instructions for the new thread:*\n{_truncate_for_slack(instructions)}"
    )


def _run_links_section(thread_id: str) -> str:
    dashboard_url = dashboard_thread_url(thread_id)
    trace_url = get_langsmith_trace_url(thread_id)
    lines = ["## Open SWE Links"]
    if dashboard_url:
        lines.append(f"- Web: {dashboard_url}")
    if trace_url:
        lines.append(f"- Trace: {trace_url}")
    lines.append(
        "- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked."
    )
    return "\n".join(lines)


def _run_prompt(
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
        f"- Thread TS: {thread_ts}\n\n"
        f"{_run_links_section(thread_id)}\n\n"
        "## Breakout Instructions\n"
        f"{instructions}\n\n"
        "Use `slack_thread_reply` to communicate in this new Slack thread for clarifications, "
        "status updates, and final summaries. For Slack requests that require non-trivial work, "
        "post a very short acknowledgement like `On it!` as soon as possible before "
        "cloning/checking out repositories, then continue."
    )


def _new_slack_thread_context(
    original: dict[str, Any],
    *,
    channel_id: str,
    thread_ts: str,
) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "triggering_user_id": original.get("triggering_user_id", ""),
        "triggering_user_name": original.get("triggering_user_name", ""),
        "triggering_user_email": original.get("triggering_user_email", ""),
        "triggering_event_ts": thread_ts,
    }


async def slack_start_new_thread(
    title: str,
    instructions: str,
    default_repo: str | None = None,
) -> dict[str, Any]:
    """Start a new Open SWE thread in a top-level Slack message in the current channel."""
    config = get_config()
    configurable = config.get("configurable", {})
    current_slack_thread = configurable.get("slack_thread")
    if not isinstance(current_slack_thread, dict):
        return {"success": False, "error": "Missing slack_thread config"}

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

    repo = _resolve_repo(configurable, default_repo)
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
        github_login = configurable.get("github_login")
        if not isinstance(github_login, str) or not github_login.strip():
            return {
                "success": False,
                "error": (
                    "Cannot verify access to the requested repository: no github_login on the "
                    "parent thread"
                ),
            }
        try:
            await require_repo_access_for_user(
                github_login.strip(), f"{repo['owner']}/{repo['name']}"
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

    message_ts, slack_error = await post_slack_top_level_message_with_ts(
        channel_id.strip(),
        _visible_message(clean_title, clean_instructions, repo),
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

    thread_id = generate_thread_id_from_slack_thread(channel_id.strip(), message_ts)
    new_slack_thread = _new_slack_thread_context(
        current_slack_thread,
        channel_id=channel_id.strip(),
        thread_ts=message_ts,
    )
    breakout_from = {
        "channel_id": channel_id.strip(),
        "thread_ts": current_thread_ts or "",
        "message_ts": current_slack_thread.get("triggering_event_ts", ""),
    }

    metadata: dict[str, Any] = {
        "source": "slack",
        "title": clean_title[:80],
        "source_context": {
            "slack_thread": new_slack_thread,
            "breakout_from": breakout_from,
        },
    }
    if repo:
        metadata.update(
            {
                "repo": repo,
                "repo_owner": repo["owner"],
                "repo_name": repo["name"],
            }
        )
    github_login = configurable.get("github_login")
    if isinstance(github_login, str) and github_login:
        metadata["github_login"] = github_login
    user_email = configurable.get("user_email")
    if isinstance(user_email, str) and user_email:
        metadata["triggering_user_email"] = user_email.strip().lower()

    new_configurable: dict[str, Any] = {
        "slack_thread": new_slack_thread,
        "source": "slack",
    }
    if repo:
        new_configurable["repo"] = repo
    for key in ("user_email", "github_login", "agent_model_id", "agent_effort"):
        value = configurable.get(key)
        if value:
            new_configurable[key] = value

    client = get_client(url=LANGGRAPH_URL)
    await client.threads.create(thread_id=thread_id, if_exists="do_nothing", metadata=metadata)
    await client.threads.update(thread_id=thread_id, metadata=metadata)

    run = await dispatch_agent_run(
        thread_id,
        _run_prompt(clean_title, clean_instructions, repo, current_slack_thread, thread_id),
        new_configurable,
        source="slack",
        client=client,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id:
        await store_slack_run_mapping(
            client,
            channel_id.strip(),
            message_ts,
            run_id,
            message_ts=message_ts,
            triggering_user_id=new_slack_thread.get("triggering_user_id") or None,
        )

    return {
        "success": True,
        "thread_id": thread_id,
        "thread_ts": message_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
