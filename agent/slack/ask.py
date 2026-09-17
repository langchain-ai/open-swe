"""The `/oswe` slash command: one question, one ephemeral answer, no Slack thread.

Each invocation gets an agent thread of its own. The thread is real — it carries
the usual `Open in Web` link and can be pinned or continued on the dashboard —
but it is stamped ``unlisted`` so one-off questions never fill anyone's thread
list. Continuing it on the web clears that stamp and the thread becomes an
ordinary dashboard one.
"""

import logging
import uuid
from typing import Any

from pydantic import BaseModel

from agent.dispatch import dispatch_agent_run
from agent.prompts import render_prompt
from agent.slack.client import (
    fetch_slack_channel_messages,
    format_slack_messages_for_prompt,
    get_slack_user_info,
    get_slack_user_names,
    post_slack_ephemeral_message,
    slack_channel_allows_operations,
)
from agent.slack.webhook import workspace_scoped_default_repo
from agent.source_context import SlackThreadRef, SourceContext
from agent.webhooks import common
from agent.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

ASK_COMMAND = "/oswe"
MAX_QUESTION_CHARS = 2000
CHANNEL_CONTEXT_MESSAGE_LIMIT = 30
CHANNEL_CONTEXT_MAX_TOKENS = 5000
# No tokenizer in this process, and a Slack transcript is plain prose, so four
# characters to the token holds the budget closely enough.
_CHANNEL_CONTEXT_MAX_CHARS = CHANNEL_CONTEXT_MAX_TOKENS * 4
_CHANNEL_CONTEXT_TRIMMED = "[earlier messages omitted to stay inside the context budget]"
_NO_CHANNEL_CONTEXT = "(unavailable — this is not a public channel, or it has no messages)"
_CHANNEL_REFUSAL = "Open SWE cannot answer questions in this channel."
_START_FAILURE = "Open SWE could not start that request. Try again in a moment."


class SlackAskRequest(BaseModel):
    channel_id: str
    user_id: str
    question: str
    thread_id: str
    command: str = ASK_COMMAND
    team_id: str = ""


def ask_thread_id(channel_id: str, user_id: str, invocation: str) -> str:
    """The thread for one slash-command invocation.

    Derived rather than stored so the route can link to it inside Slack's three
    seconds. `invocation` is unique per command, so two questions never share a
    thread, and a redelivery of the same command resolves back to the first.
    """
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-ask:{channel_id}:{user_id}:{invocation}")
    )


async def _slack_user_profile(user_id: str) -> tuple[str, str]:
    """``(display name, email)`` for a Slack user, both best-effort."""
    info = await get_slack_user_info(user_id)
    profile = info.get("profile") if isinstance(info, dict) else None
    if not isinstance(profile, dict):
        return "", ""
    name = profile.get("display_name") or profile.get("real_name") or ""
    email = profile.get("email") or ""
    return (name if isinstance(name, str) else ""), (email if isinstance(email, str) else "")


def _channel_label(channel_context: dict[str, Any] | None) -> str:
    """`` (#eng)`` when Slack names the channel, empty when it does not."""
    if not isinstance(channel_context, dict):
        return ""
    for key in ("name_normalized", "name"):
        value = channel_context.get(key)
        if isinstance(value, str) and value.strip():
            return f" (#{value.strip()})"
    return ""


async def _channel_context(channel_id: str) -> str:
    """Recent channel messages, oldest trimmed away until they fit the budget."""
    messages = await fetch_slack_channel_messages(channel_id, CHANNEL_CONTEXT_MESSAGE_LIMIT)
    if not messages:
        return ""
    user_ids = [
        user_id for msg in messages if isinstance(user_id := msg.get("user"), str) and user_id
    ]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}
    transcript = format_slack_messages_for_prompt(messages, user_names, include_thread_replies=True)
    kept: list[str] = []
    remaining = _CHANNEL_CONTEXT_MAX_CHARS - len(_CHANNEL_CONTEXT_TRIMMED) - 1
    for line in reversed(transcript.splitlines()):
        remaining -= len(line) + 1
        if remaining < 0:
            kept.append(_CHANNEL_CONTEXT_TRIMMED)
            break
        kept.append(line)
    return "\n".join(reversed(kept))


async def _refuse(request: SlackAskRequest, text: str) -> None:
    await post_slack_ephemeral_message(request.channel_id, request.user_id, text)


async def _runnable_login(request: SlackAskRequest, login: str | None, email: str) -> str | None:
    """The GitHub login to run as, or None once the asker has been asked to link one."""
    if login:
        try:
            if await common.get_valid_access_token(login):
                return login
        except Exception:  # noqa: BLE001
            logger.debug("Could not resolve a GitHub token for %s", login, exc_info=True)
    has_record = False
    if login:
        try:
            has_record = await common.has_access_token_record(login)
        except Exception:  # noqa: BLE001
            logger.debug("Could not check the GitHub token record for %s", login, exc_info=True)
    await common.post_account_link_prompt(
        request.channel_id,
        "",
        request.user_id,
        email or None,
        reason="revoked" if has_record else "unlinked",
        ephemeral=True,
    )
    return None


async def _process_slack_ask(request: SlackAskRequest) -> None:
    channel_context = await common.resolve_slack_channel_context(request.channel_id)
    if not slack_channel_allows_operations(channel_context):
        await _refuse(request, _CHANNEL_REFUSAL)
        return

    user_name, user_email = await _slack_user_profile(request.user_id)
    login = await _runnable_login(
        request,
        await common.login_for_slack_id(request.user_id)
        or (await common.login_for_email(user_email) if user_email else None),
        user_email,
    )
    if login is None:
        return

    thread_id = request.thread_id
    # A repository the channel names owns the routing decision, so it has to be
    # resolved before the workspace is.
    resolution = await common.get_slack_repo_config(
        request.channel_id,
        "",
        slack_user_id=request.user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    workspace = (
        await resolve_workspace(
            repo=resolution.routing_repo,
            slack_channel_id=request.channel_id,
            login=login,
        )
    ).slug
    resolved_repo = resolution.repo
    if resolved_repo is not None and not resolution.explicit:
        resolved_repo = await workspace_scoped_default_repo(resolved_repo, workspace)
    repo = resolved_repo.model_dump() if resolved_repo else None
    slack_thread = SlackThreadRef(
        channel_id=request.channel_id,
        triggering_user_id=request.user_id,
        triggering_user_name=user_name,
        triggering_user_email=user_email,
        team_id=request.team_id,
        channel_context=channel_context,
    )
    # Private, always: a slash command is invisible to the channel and the answer
    # goes only to the asker, and a private thread is what scopes the run to their
    # own credentials, skills, and instructions.
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="slack",
        repo_config=repo,
        github_login=login,
        user_email=user_email,
        title=request.question,
        source_context=SourceContext(slack_thread=slack_thread),
        workspace=workspace,
        visibility="private",
        owner_login=login,
        unlisted=True,
    )
    if not persisted:
        await _refuse(request, _START_FAILURE)
        return

    configurable: dict[str, Any] = {
        "repo": repo,
        "slack_thread": slack_thread.dump(),
        "source": "slack",
        "slack_ask": True,
        "plan_mode": False,
        "github_login": login,
        "user_email": user_email,
        "workspace": workspace,
        "environment": workspace,
    }
    prompt = render_prompt(
        "runs/slack-ask.md",
        command=request.command,
        asked_by=user_name or f"<@{request.user_id}>",
        request=request.question,
        channel_id=request.channel_id,
        channel_name=_channel_label(channel_context),
        channel_context=await _channel_context(request.channel_id) or _NO_CHANNEL_CONTEXT,
    )
    await dispatch_agent_run(thread_id, prompt, configurable, source="slack")
    logger.info(
        "Started a Slack slash command run",
        extra={"agent_thread_id": thread_id, "slack_channel": request.channel_id},
    )


async def process_slack_ask(request: SlackAskRequest) -> None:
    """Answer one `/oswe` question, reporting any failure back to the asker."""
    try:
        await _process_slack_ask(request)
    except Exception:
        logger.exception(
            "Failed to answer a Slack question",
            extra={"slack_channel": request.channel_id},
        )
        try:
            await _refuse(request, _START_FAILURE)
        except Exception:  # noqa: BLE001
            logger.debug("Could not report the question failure to Slack", exc_info=True)
