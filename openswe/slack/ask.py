"""One-off questions: the `/oswe` slash command and `@Open SWE /btw`.

Each invocation gets an agent thread of its own, private to the asker and stamped
``unlisted`` so one-off questions never fill anyone's thread list. `/oswe` answers
ephemerally with the usual `Open in Web` link, and continuing it on the web clears
that stamp. `/btw` answers in the Slack thread for everyone, without a link, and
never touches the agent thread that Slack thread already maps to.
"""

import logging
import re
import uuid
from typing import Any

from pydantic import BaseModel

from openswe.dispatch import dispatch_agent_run
from openswe.prompts import prompt
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    acknowledge_slack_command,
    add_slack_reaction,
    clear_slack_command_message,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_info,
    get_slack_user_names,
    post_slack_ephemeral_message,
    remove_slack_reaction,
    replace_slack_command_message,
)
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.slack.thinking import settle_slack_thread_status
from openswe.slack.webhook import workspace_scoped_default_repo
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.users import User
from openswe.utils.message_commands import BY_THE_WAY
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

ASK_COMMAND = "/oswe"
BY_THE_WAY_COMMAND = BY_THE_WAY.name
_CONTEXT_FENCE_RE = re.compile(r"<(\s*/?\s*untrusted_slack_context)", re.IGNORECASE)
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
_ACKNOWLEDGEMENT = "Working on it — the answer will replace this message, visible only to you."
_BY_THE_WAY_USAGE = "Ask a question after it: `@Open SWE /btw how does thread routing work?`"
_BY_THE_WAY_TOO_LONG = (
    "That question is too long for `/btw`. Tag Open SWE without `/btw` to start a thread instead."
)


class SlackAskRequest(BaseModel):
    channel_id: str
    user_id: str
    question: str
    thread_id: str
    command: str = ASK_COMMAND
    team_id: str = ""
    response_url: str = ""
    # `/btw` only: the Slack thread the public answer goes to, and the mention's own ts.
    reply_thread_ts: str = ""
    message_ts: str = ""

    @staticmethod
    def by_the_way_question(text: str, bot_user_id: str) -> str | None:
        """The question in an `@Open SWE /btw ...` mention; None when it is not one."""
        parsed = BY_THE_WAY.parse(text, bot_user_id, common.SLACK_BOT_USERNAME)
        return parsed.argument if parsed is not None else None

    @property
    def by_the_way(self) -> bool:
        return bool(self.reply_thread_ts)

    @property
    def in_slack_thread(self) -> bool:
        """Whether `/btw` was sent as a reply, so the thread rather than the channel is context."""
        return self.by_the_way and self.reply_thread_ts != self.message_ts


def ask_thread_id(channel_id: str, user_id: str, invocation: str) -> str:
    """The thread for one `/oswe` or `/btw` invocation.

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


def _channel_label(channel_context: SlackChannelContext) -> str:
    """`` (#eng)`` when Slack names the channel, empty when it does not."""
    label = channel_context.name_normalized.strip() or channel_context.name.strip()
    return f" (#{label})" if label else ""


async def _channel_context(channel_id: str) -> str:
    """Recent channel messages, oldest trimmed away until they fit the budget."""
    channel = await SlackChannel.load(channel_id)
    messages = await channel.messages(CHANNEL_CONTEXT_MESSAGE_LIMIT) if channel else []
    return await _budgeted_transcript(messages)


async def _thread_context(channel_id: str, thread_ts: str) -> str:
    """The Slack thread `/btw` was sent in, oldest trimmed away until it fits the budget."""
    raw = await fetch_slack_thread_messages(channel_id, thread_ts)
    messages = [
        message
        for item in raw
        if (message := SlackMessage.parse(item)) is not None and not message.is_noise
    ]
    return await _budgeted_transcript(messages)


async def _request_context(request: SlackAskRequest) -> str:
    if request.in_slack_thread:
        context = await _thread_context(request.channel_id, request.reply_thread_ts)
    else:
        context = await _channel_context(request.channel_id)
    return _CONTEXT_FENCE_RE.sub(r"&lt;\1", context)


async def _budgeted_transcript(messages: list[SlackMessage]) -> str:
    if not messages:
        return ""
    user_ids = [message.user for message in messages if message.user]
    user_names = await get_slack_user_names(user_ids) if user_ids else {}
    transcript = format_slack_messages_for_prompt(
        [message.dump() for message in messages], user_names, include_thread_replies=True
    )
    kept: list[str] = []
    remaining = _CHANNEL_CONTEXT_MAX_CHARS - len(_CHANNEL_CONTEXT_TRIMMED) - 1
    for line in reversed(transcript.splitlines()):
        remaining -= len(line) + 1
        if remaining < 0:
            kept.append(_CHANNEL_CONTEXT_TRIMMED)
            break
        kept.append(line)
    return "\n".join(reversed(kept))


async def _settle_thinking_status(request: SlackAskRequest) -> None:
    if request.by_the_way:
        await remove_slack_reaction(
            request.channel_id, request.message_ts, "hourglass_flowing_sand"
        )
        await settle_slack_thread_status(request.channel_id, request.reply_thread_ts)


async def _refuse(request: SlackAskRequest, text: str) -> None:
    await _settle_thinking_status(request)
    if request.response_url and await replace_slack_command_message(request.response_url, text):
        return
    await post_slack_ephemeral_message(
        request.channel_id, request.user_id, text, request.reply_thread_ts or None
    )


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
    await _settle_thinking_status(request)
    await common.post_account_link_prompt(
        request.channel_id,
        request.reply_thread_ts,
        request.user_id,
        email or None,
        reason="revoked" if has_record else "unlinked",
        ephemeral=True,
    )
    if request.response_url:
        await clear_slack_command_message(request.response_url)
    return None


async def _process_slack_ask(request: SlackAskRequest) -> None:
    if request.response_url:
        await acknowledge_slack_command(request.response_url, _ACKNOWLEDGEMENT)
    if request.by_the_way:
        await add_slack_reaction(request.channel_id, request.message_ts, "hourglass_flowing_sand")
        if not request.question:
            await _refuse(request, _BY_THE_WAY_USAGE)
            return
        if len(request.question) > MAX_QUESTION_CHARS:
            await _refuse(request, _BY_THE_WAY_TOO_LONG)
            return
    channel_context = await common.resolve_slack_channel_context(request.channel_id)
    if not channel_context.allows_operations:
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
    source_context = SourceContext(
        slack_thread=slack_thread,
        slack_ask=True,
        slack_ask_response_url=request.response_url,
        slack_by_the_way_thread_ts=request.reply_thread_ts,
        slack_by_the_way_message_ts=request.message_ts,
    )
    # Private, always, even when `/btw` answers publicly: a private thread is what
    # scopes the run to the asker's own credentials, skills, and instructions.
    # `slack_thread` carries no `thread_ts`, or the Slack thread would resolve here.
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="slack",
        repo_config=repo,
        github_login=login,
        user_email=user_email,
        title=request.question,
        source_context=source_context,
        workspace=workspace,
        visibility="private",
        owner_login=login,
        unlisted=True,
    )
    if not persisted:
        await _refuse(request, _START_FAILURE)
        return

    configurable: dict[str, Any] = {
        **source_context.dump(),
        "repo": repo,
        "source": "slack",
        "github_login": login,
        "user_email": user_email,
        "workspace": workspace,
        "environment": workspace,
    }
    run_prompt = prompt(
        "runs/slack-by-the-way" if request.by_the_way else "runs/slack-ask",
        command=request.command,
        asked_by=user_name or f"<@{request.user_id}>",
        request=request.question,
        channel_id=request.channel_id,
        channel_name=_channel_label(channel_context),
        in_slack_thread=request.in_slack_thread,
        channel_context=await _request_context(request) or _NO_CHANNEL_CONTEXT,
    )
    await dispatch_agent_run(thread_id, run_prompt, configurable, source="slack", thread_title=None)
    logger.info(
        "Started a one-off Slack question run",
        extra={
            "agent_thread_id": thread_id,
            "slack_channel": request.channel_id,
            "by_the_way": request.by_the_way,
        },
    )


async def process_slack_ask(request: SlackAskRequest) -> None:
    """Answer one `/oswe` or `/btw` question, reporting any failure back to the asker."""
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
