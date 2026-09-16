"""The `/oswe` slash command: one question, one ephemeral answer, no Slack thread.

The agent thread behind the answer is real — it carries the usual `Open in Web`
link and can be pinned or continued on the dashboard — but it is stamped
``unlisted`` so one-off questions never fill anyone's thread list. Continuing it
on the web clears that stamp and the thread becomes an ordinary dashboard one.
"""

import logging
import uuid
from typing import Any

from pydantic import BaseModel

from agent.dispatch import dispatch_agent_run
from agent.prompts import render_prompt
from agent.slack.client import (
    get_slack_user_info,
    post_slack_ephemeral_message,
    slack_channel_allows_operations,
)
from agent.slack.webhook import workspace_scoped_default_repo
from agent.source_context import SlackThreadRef, SourceContext
from agent.users import User
from agent.utils.thread_ops import get_thread_active_status, queue_message_for_thread
from agent.webhooks import common
from agent.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

ASK_COMMAND = "/oswe"
MAX_QUESTION_CHARS = 2000
_CHANNEL_REFUSAL = "Open SWE cannot answer questions in this channel."
_START_FAILURE = "Open SWE could not start that request. Try again in a moment."
_QUEUED = "Added to what I'm already working on for you here."


class SlackAskRequest(BaseModel):
    channel_id: str
    user_id: str
    question: str
    thread_id: str
    command: str = ASK_COMMAND
    team_id: str = ""


def ask_thread_id(channel_id: str, user_id: str) -> str:
    """The one scratch thread this person's slash commands share in this channel.

    Derived rather than stored so the route can link to it inside Slack's three
    seconds, and so every later command lands in the same conversation.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-ask:{channel_id}:{user_id}"))


async def _slack_user_profile(user_id: str) -> tuple[str, str]:
    """``(display name, email)`` for a Slack user, both best-effort."""
    info = await get_slack_user_info(user_id)
    profile = info.get("profile") if isinstance(info, dict) else None
    if not isinstance(profile, dict):
        return "", ""
    name = profile.get("display_name") or profile.get("real_name") or ""
    email = profile.get("email") or ""
    return (name if isinstance(name, str) else ""), (email if isinstance(email, str) else "")


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
        await User.login_for_slack(request.user_id)
        or (await User.login_for_email(user_email) if user_email else None),
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
    )
    # The thread is shared by every command this person runs in this channel, so
    # a command sent while the last one is still working joins it instead of
    # interrupting the work in flight.
    if await get_thread_active_status(thread_id) and await queue_message_for_thread(
        thread_id, [{"type": "text", "text": prompt}]
    ):
        await _refuse(request, _QUEUED)
        logger.info(
            "Queued a Slack slash command",
            extra={"agent_thread_id": thread_id, "slack_channel": request.channel_id},
        )
        return

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
