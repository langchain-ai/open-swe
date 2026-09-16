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
from agent.webhooks import common
from agent.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

ASK_COMMAND = "/oswe"
MAX_QUESTION_CHARS = 2000
_CHANNEL_REFUSAL = "Open SWE cannot answer questions in this channel."
_START_FAILURE = "Open SWE could not start that question. Try again in a moment."


class SlackAskRequest(BaseModel):
    channel_id: str
    user_id: str
    question: str
    command: str = ASK_COMMAND
    team_id: str = ""


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


async def _answerable(request: SlackAskRequest, login: str | None, email: str) -> bool:
    """Whether the asker is linked to a GitHub account Open SWE can run as."""
    if login:
        try:
            if await common.get_valid_access_token(login):
                return True
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
    return False


async def _process_slack_ask(request: SlackAskRequest) -> None:
    channel_context = await common.resolve_slack_channel_context(request.channel_id)
    if not slack_channel_allows_operations(channel_context):
        await _refuse(request, _CHANNEL_REFUSAL)
        return

    user_name, user_email = await _slack_user_profile(request.user_id)
    login = await common.login_for_slack_id(request.user_id) or (
        await common.login_for_email(user_email) if user_email else None
    )
    if not await _answerable(request, login, user_email):
        return

    thread_id = str(uuid.uuid4())
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
    visibility = "private" if channel_context.get("is_im") is True else "public"
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="slack",
        repo_config=repo,
        github_login=login or "",
        user_email=user_email,
        title=request.question,
        source_context=SourceContext(slack_thread=slack_thread),
        workspace=workspace,
        visibility=visibility,
        owner_login=login or "",
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
        "user_email": user_email,
        "workspace": workspace,
        "environment": workspace,
    }
    if login:
        configurable["github_login"] = login
    await dispatch_agent_run(
        thread_id,
        render_prompt(
            "runs/slack-ask.md",
            asked_by=user_name or f"<@{request.user_id}>",
            question=request.question,
        ),
        configurable,
        source="slack",
    )
    logger.info(
        "Started a Slack question run",
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
