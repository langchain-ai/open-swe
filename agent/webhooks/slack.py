"""Slack webhook handler — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from langchain_core.messages.content import create_text_block

from agent.dashboard.environments import ENVIRONMENTS, parse_environment_tag
from agent.input_messages import (
    InputMessageContext,
    MessageKind,
    PersonIdentity,
    RunInput,
    SystemIdentity,
    channel_introduction,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from agent.source_context import SlackThreadRef, SourceContext
from agent.spawn import CodeChannelError, SpawnOrigin, open_code_channel
from agent.surfaces import slack_surface
from agent.utils import slack as slack_utils
from agent.utils.json_types import as_json_object
from agent.utils.langsmith import get_langsmith_trace_url
from agent.utils.slack_code_channels import CODE_CHANNEL_SESSION_TS
from agent.utils.thread_ops import (
    langgraph_client as get_langgraph_client,
)
from agent.utils.thread_ops import queue_message_for_thread

from ..utils.user_messages import warning
from . import common

STALE_PARTICIPANT_SECONDS = 15 * 60
RAPID_FOLLOWUP_SECONDS = 60
_MENTION_PREAMBLE = "You were mentioned in Slack.\n\n"

_UNTAGGED_REPLY_PREAMBLE = (
    "A message arrived in a Slack thread you are part of. You were NOT tagged in it — "
    "you are seeing it because you and the sender are the only active participants.\n\n"
    "Decide first whether the message is actually addressed to you. Continuations of your "
    "conversation, answers to your questions, and follow-up instructions are addressed to you. "
    "Someone thinking out loud, talking to another person, or commenting on the thread without "
    "expecting you to act is not.\n\n"
    "If it is not addressed to you, end your turn without calling any tool and post nothing, "
    "including no reaction. Staying silent is the right outcome; an unwanted reply or reaction "
    "from an untagged message is worse than no reply. If it is "
    "addressed to you, handle it exactly as you would a direct mention.\n\n"
)

_MESSAGE_UPDATE_PREAMBLE = (
    "A Slack message previously delivered to this thread was edited. Treat the updated text "
    "below as the current version and as an explicit correction to the earlier message. Do not "
    "repeat completed work unless the update requires it.\n\n"
)


def _slack_prompt_preamble(untagged_reply: bool, message_update: bool = False) -> str:
    if message_update:
        return _MESSAGE_UPDATE_PREAMBLE
    return _UNTAGGED_REPLY_PREAMBLE if untagged_reply else _MENTION_PREAMBLE


def _slack_request_heading(untagged_reply: bool, message_update: bool = False) -> str:
    if message_update:
        return "## Updated Slack Message"
    return "## Untagged Message" if untagged_reply else "## Latest Mention Request"


def _is_explicit_slack_request(
    text: str,
    bot_user_id: str,
    *,
    treat_all_messages_as_mentions: bool,
    message_update: bool,
) -> bool:
    return not message_update and bool(
        treat_all_messages_as_mentions
        or (bot_user_id and f"<@{bot_user_id}>" in text)
        or (common.SLACK_BOT_USERNAME and f"@{common.SLACK_BOT_USERNAME}" in text)
    )


def _interrupts_active_run(
    text: str,
    bot_user_id: str,
    *,
    treat_all_messages_as_mentions: bool,
    code_channel: bool,
    message_update: bool,
    explicit_request: bool,
) -> bool:
    return explicit_request or _is_explicit_slack_request(
        text,
        bot_user_id,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions and not code_channel,
        message_update=message_update,
    )


async def _slack_thread_allows_untagged_reply(
    channel_id: str,
    thread_ts: str,
    text: str,
    bot_user_id: str,
    sender_id: str = "",
    now_ts: str = "",
) -> bool:
    """Allow an untagged follow-up when the sender and Open SWE are the live participants."""
    if not channel_id or not thread_ts or not bot_user_id:
        return False

    mentioned = set(re.findall(r"<@([A-Z0-9_]+)", text or ""))
    if any(user_id != bot_user_id for user_id in mentioned):
        return False

    messages = await common.fetch_slack_thread_messages(channel_id, thread_ts)
    bot_last_ts = 0.0
    current_ts = common._parse_ts(now_ts)
    latest_ts = current_ts
    last_message_ts: dict[str, float] = {}
    human_messages: list[tuple[float, str, str]] = []
    for message in messages:
        message_ts = common._parse_ts(message.get("ts"))
        latest_ts = max(latest_ts, message_ts)
        author = message.get("user")
        if author == bot_user_id:
            bot_last_ts = max(bot_last_ts, message_ts)
            continue
        if message.get("bot_id") or message.get("subtype"):
            continue
        if isinstance(author, str) and author:
            last_message_ts[author] = max(last_message_ts.get(author, 0.0), message_ts)
            message_text = message.get("text")
            human_messages.append(
                (message_ts, author, message_text if isinstance(message_text, str) else "")
            )

    if not last_message_ts:
        return False

    sender = sender_id or max(last_message_ts, key=lambda author: last_message_ts[author])
    if not bot_last_ts:
        if not sender or not current_ts:
            return False
        timeline = sorted(
            (message for message in human_messages if message[0] <= current_ts),
            key=lambda message: message[0],
        )
        if not any(
            message_ts == current_ts and author == sender for message_ts, author, _ in timeline
        ):
            timeline.append((current_ts, sender, text))
        mention_tokens = [f"<@{bot_user_id}>"]
        if common.SLACK_BOT_USERNAME:
            mention_tokens.append(f"@{common.SLACK_BOT_USERNAME}")
        mention_index = next(
            (
                index
                for index in range(len(timeline) - 1, -1, -1)
                if timeline[index][1] == sender
                and any(token in timeline[index][2] for token in mention_tokens)
            ),
            -1,
        )
        if mention_index < 0:
            return False
        followups = timeline[mention_index:]
        return all(author == sender for _, author, _ in followups) and all(
            0 <= following[0] - previous[0] <= RAPID_FOLLOWUP_SECONDS
            for previous, following in zip(followups, followups[1:], strict=False)
        )

    return not any(
        author != sender
        and not (author_ts < bot_last_ts and latest_ts - author_ts > STALE_PARTICIPANT_SECONDS)
        for author, author_ts in last_message_ts.items()
    )


async def _dispatch_or_queue_slack_run(
    client: Any,
    thread_id: str,
    run_input: RunInput | list[dict[str, Any]],
    configurable: dict[str, Any],
    *,
    explicitly_tagged: bool,
) -> dict[str, Any]:
    """Dispatch explicit requests immediately and enqueue other Slack follow-ups."""
    if isinstance(run_input, list):
        run_input = {"messages": cast(list[Any], run_input)}
    return as_json_object(
        await common.dispatch_agent_run(
            thread_id,
            None,
            configurable,
            source="slack",
            input=run_input,
            metadata=common._AGENT_VERSION_METADATA,
            client=client,
            multitask_strategy="interrupt" if explicitly_tagged else "enqueue",
        )
    )


async def _slack_user_can_reply_to_ready_plan(
    channel_id: str, thread_ts: str, slack_user_id: str
) -> bool:
    if not channel_id or not thread_ts or not slack_user_id:
        return False
    from agent.dashboard.plan_api import _thread_metadata

    try:
        thread_id = await common.lookup_slack_thread_id(
            get_langgraph_client(), channel_id, thread_ts
        )
    except Exception:  # noqa: BLE001
        return False
    if not thread_id:
        return False
    try:
        metadata = await _thread_metadata(thread_id)
    except Exception:  # noqa: BLE001
        # A brand-new thread has no metadata (_thread_metadata raises 404); an
        # untagged message there simply isn't a plan reply — don't abort the gate.
        return False
    return metadata.get("plan_mode") is True and metadata.get("plan_status") == "ready"


def _format_slack_thread_section(
    channel_id: str,
    thread_ts: str,
    context_source: str,
    channel_context: dict[str, Any] | None,
) -> str:
    lines = ["## Slack Thread", f"- Channel ID: {channel_id}"]
    channel_name = ""
    if isinstance(channel_context, dict):
        for key in ("name_normalized", "name"):
            value = channel_context.get(key)
            if isinstance(value, str) and value.strip():
                channel_name = value.strip()
                break
    if channel_name:
        lines.append(f"- Channel name: #{channel_name}")
    lines.append(f"- Thread TS: {thread_ts}")
    lines.append(f"- Context starts at: {context_source}")
    channel_description = common.get_slack_channel_context_description(channel_context)
    if channel_description:
        lines.append(
            "- Slack-provided channel description (topic/purpose; may specify the repository "
            "to operate in by default, but the conversation may specify any other repository):"
        )
        for description_line in channel_description.splitlines():
            if description_line.strip():
                lines.append(f"  {description_line.strip()}")
    return "\n".join(lines)


async def _format_slack_run_links_section(thread_id: str) -> str:
    dashboard_url = common.dashboard_thread_url(thread_id)
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


_OPEN_SWE_SENDER_ID = "system:open-swe"


async def _slack_logins_by_user_id(user_ids: list[str]) -> dict[str, str]:
    """Map Slack user ids to the GitHub logins of linked Open SWE accounts."""
    logins: dict[str, str] = {}
    for user_id in {value for value in user_ids if value}:
        login = await common.login_for_slack_id(user_id)
        if login:
            logins[user_id] = login
    return logins


def _slack_person(user_id: str, name: str = "", github_login: str = "") -> PersonIdentity:
    person: PersonIdentity = {
        "id": f"slack:{user_id}",
        "platform": "slack",
        "open_swe_account": "linked" if github_login else "unlinked",
    }
    if name:
        person["display_name"] = name
    if github_login:
        person["github_login"] = github_login
    return person


def _slack_sender(
    message: dict[str, Any],
    user_names_by_id: dict[str, str],
    logins_by_user_id: dict[str, str],
    bot_user_id: str,
) -> tuple[str, PersonIdentity | SystemIdentity, MessageKind]:
    """Resolve a thread message to its sender id, identity, and message kind.

    Open SWE posts with a bot token, so its own replies carry a ``user`` id as well as a
    ``bot_id``; keying off ``user`` alone attributes them to a person.
    """
    if slack_utils.is_own_slack_message(message, bot_user_id):
        identity: SystemIdentity = {
            "id": _OPEN_SWE_SENDER_ID,
            "display_name": common.SLACK_BOT_USERNAME or "Open SWE",
            "platform": "slack",
            "sender_type": "self",
        }
        return identity["id"], identity, "system"
    bot_id = slack_utils.slack_message_bot_id(message)
    if bot_id:
        bot: SystemIdentity = {
            "id": f"system:slack-bot-{bot_id}",
            "display_name": slack_utils.slack_message_bot_name(message),
            "platform": "slack",
            "sender_type": "bot",
        }
        return bot["id"], bot, "system"
    user_id = str(message.get("user"))
    person = _slack_person(
        user_id, user_names_by_id.get(user_id, ""), logins_by_user_id.get(user_id, "")
    )
    return person["id"], person, "human"


def _slack_message_text(message: dict[str, Any], bot_user_id: str) -> str:
    forwarded = common.format_slack_messages_for_prompt(
        [message], {}, bot_user_id=bot_user_id, bot_username=common.SLACK_BOT_USERNAME
    )
    _, separator, content = forwarded.partition(": ")
    return content if separator else forwarded


def _slack_context_input(
    messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str],
    logins_by_user_id: dict[str, str],
    *,
    channel_id: str,
    bot_user_id: str,
    event_ts: str,
    trigger_user_id: str = "",
    request_text: str,
    request_blocks: list[dict[str, Any]],
    operational_context: str,
) -> RunInput:
    channel_entity_id = f"slack:{channel_id}"
    run_messages = [channel_introduction({"id": channel_entity_id, "platform": "slack"})]
    introduced: set[str] = {channel_entity_id}
    for message in messages:
        if str(message.get("ts", "")) == str(event_ts):
            continue
        sender_id, identity, kind = _slack_sender(
            message, user_names_by_id, logins_by_user_id, bot_user_id
        )
        if sender_id not in introduced:
            run_messages.append(
                person_introduction(cast(PersonIdentity, identity))
                if kind == "human"
                else system_introduction(cast(SystemIdentity, identity))
            )
            introduced.add(sender_id)
        message_context: InputMessageContext = {
            "sender_id": sender_id,
            "channel_id": channel_entity_id,
            "surface": "slack",
            "kind": kind,
            "data": {"timestamp": str(message.get("ts", ""))},
        }
        text = _slack_message_text(message, bot_user_id)
        run_messages.append(
            human_input(text, message_context)
            if kind == "human"
            else system_input(text, message_context)
        )
    run_messages.append(
        system_introduction(
            {"id": "system:slack-context", "display_name": "Slack context", "platform": "slack"}
        )
    )
    run_messages.append(
        system_input(
            operational_context,
            {
                "sender_id": "system:slack-context",
                "channel_id": channel_entity_id,
                "surface": "slack",
                "kind": "system",
            },
        )
    )
    # An edit's `event_ts` matches no message, and the approve-button path passes
    # the ts of Open SWE's own button message, so matching history attributes the
    # run to nobody or to the bot. The caller already knows who triggered it.
    trigger_id = trigger_user_id or next(
        (
            str(message.get("user"))
            for message in messages
            if str(message.get("ts", "")) == str(event_ts)
            and message.get("user")
            and not slack_utils.is_own_slack_message(message, bot_user_id)
            and not slack_utils.slack_message_bot_id(message)
        ),
        "unknown",
    )
    trigger_person = _slack_person(
        trigger_id,
        user_names_by_id.get(trigger_id, ""),
        logins_by_user_id.get(trigger_id, ""),
    )
    if trigger_person["id"] not in introduced:
        run_messages.append(person_introduction(trigger_person))
    current_message = next(
        (message for message in messages if str(message.get("ts", "")) == str(event_ts)), {}
    )
    rendered_request = _slack_message_text(current_message, bot_user_id)
    _, separator, forwarded_context = rendered_request.partition("\n")
    if separator and forwarded_context:
        request_text = f"{request_text}\n{forwarded_context}"
    request_blocks[0] = {**request_blocks[0], "text": request_text}
    run_messages.append(
        human_input(
            request_blocks,
            {
                "sender_id": trigger_person["id"],
                "channel_id": channel_entity_id,
                "surface": "slack",
                "kind": "human",
                "data": {"timestamp": event_ts},
            },
        )
    )
    return {"messages": run_messages}


async def process_slack_mention(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
    """Process a Slack request by creating a run or queuing a mid-run message."""
    try:
        await _process_slack_mention_impl(event_data, repo_config)
    except Exception:  # noqa: BLE001
        common.logger.exception("Unexpected error while processing Slack mention")
        await _notify_slack_processing_error(event_data, repo_config)


_CODE_CHANNEL_COMMAND_TITLE_CHARS = 120


def _command_title(prompt: str) -> str:
    """A provisional channel name from the prompt, until the session titles itself."""
    first = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    sentence = first.split(". ")[0].strip(" .") or first
    title = sentence[:_CODE_CHANNEL_COMMAND_TITLE_CHARS].strip()
    return title or "Open SWE task"


async def _command_handoff(*, prompt: str, repo: dict[str, str], channel_id: str, user: str) -> str:
    owner, name = repo.get("owner", ""), repo.get("name", "")
    repo_text = f"{owner}/{name}" if owner and name else "(no repository specified)"
    return "\n\n".join(
        (
            "You are the agent for a new Slack code channel, opened by a command in "
            f"<#{channel_id}>. The whole channel is one session with you.",
            f"## Task\n{prompt}",
            f"## Default Repository Hint\n{repo_text}\n"
            "Use this repository unless the task clearly identifies a different one.",
            "## Checkout\n- Nothing has been started yet: this session has its own fresh "
            "sandbox and a clean checkout.",
            f"## Origin\n- Asked for by {user}\n"
            "- Nothing was posted in the channel it was asked from, so this channel is "
            "where the work and the conversation happen.",
        )
    )


async def process_code_channel_command(command: dict[str, Any]) -> None:
    """Open a code channel for a slash command, and tell the caller where it is.

    The command leaves no message behind in the channel it was typed in, so
    there is no origin pair to inherit privacy or invite anyone: the caller is
    invited directly, and the ephemeral reply carries the link either way.
    """
    channel_id = str(command.get("channel_id") or "")
    user_id = str(command.get("user_id") or "")
    prompt = str(command.get("text") or "").strip()
    response_url = str(command.get("response_url") or "")
    repo_config = command.get("repo") if isinstance(command.get("repo"), dict) else {}
    title = _command_title(prompt)

    try:
        user_name = ""
        slack_user = await common.get_slack_user_info(user_id) if user_id else None
        if isinstance(slack_user, dict):
            profile = slack_user.get("profile")
            profile = profile if isinstance(profile, dict) else {}
            user_name = str(
                profile.get("display_name")
                or profile.get("real_name")
                or slack_user.get("name")
                or ""
            )
        login = await common.login_for_slack_id(user_id)
        opened = await open_code_channel(
            get_langgraph_client(),
            title=title,
            content=await _command_handoff(
                prompt=prompt,
                repo=cast(dict[str, str], repo_config),
                channel_id=channel_id,
                user=user_name or f"<@{user_id}>",
            ),
            repo=cast(dict[str, str], repo_config) or None,
            # Whoever ran the command, plus anyone they named in it.
            invite=[user_id, *re.findall(r"<@([A-Z0-9]+)", prompt)],
            origin=SpawnOrigin.from_config(
                {"github_login": login or "", "user_email": ""},
                SlackThreadRef(
                    channel_id=channel_id,
                    triggering_user_id=user_id,
                    triggering_user_name=user_name,
                ),
            ),
            source_context={"opened_by_command": {"channel_id": channel_id, "user_id": user_id}},
            team_id=str(command.get("team_id") or ""),
        )
    except CodeChannelError as exc:
        await common.respond_to_slack_command(
            response_url, f"Could not open a code channel: {exc.message}"
        )
        return
    except Exception:
        common.logger.exception("Unexpected error while opening a code channel by command")
        await common.respond_to_slack_command(
            response_url, "Could not open a code channel. The failure is in the Open SWE logs."
        )
        return

    # Opening the channel is what puts people in it; the link is what gets the
    # caller there if that failed.
    await common.respond_to_slack_command(response_url, f"Working on it in <#{opened.channel_id}>.")


async def process_slack_plan_approval(
    event_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    from agent.dashboard.plan_api import approve_plan_for_thread
    from agent.dashboard.plan_store import make_plan_approver

    try:
        user_id = str(event_data.get("user_id") or "")
        user_name = str(event_data.get("user_name") or "")
        await approve_plan_for_thread(
            str(event_data.get("thread_id") or ""),
            approver=make_plan_approver(
                actor_id=user_id,
                name=user_name or user_id or "Slack user",
                source="slack",
            ),
        )
    except Exception:  # noqa: BLE001
        common.logger.exception("Unexpected error while processing Slack plan approval")
        await _notify_slack_processing_error(event_data, repo_config)


async def _notify_slack_processing_error(
    event_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    bot_user_id = event_data.get("bot_user_id", "")
    if not channel_id or not thread_ts:
        return

    thread_id = event_data.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        try:
            thread_id = await common.lookup_slack_thread_id(
                get_langgraph_client(), channel_id, thread_ts
            )
        except Exception:  # noqa: BLE001
            thread_id = None
    if not thread_id:
        return
    try:
        clean_text = (
            common.strip_bot_mention(text, bot_user_id, bot_username=common.SLACK_BOT_USERNAME)
            or "Slack request"
        )
        await common.upsert_agent_thread_metadata(
            thread_id,
            source="slack",
            repo_config=repo_config,
            title=clean_text,
            source_context=SourceContext(
                slack_thread=SlackThreadRef(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    triggering_user_id=user_id,
                    triggering_event_ts=event_ts,
                )
            ),
        )
    except Exception:  # noqa: BLE001
        common.logger.warning(
            "Could not persist Slack error metadata for thread %s", thread_id, exc_info=True
        )

    try:
        await get_langgraph_client().threads.update(
            thread_id=thread_id,
            metadata={
                "latest_run_status": "error",
                "updated_at_ms": int(datetime.now(UTC).timestamp() * 1000),
            },
        )
    except Exception:  # noqa: BLE001
        common.logger.warning("Could not mark Slack thread %s as errored", thread_id, exc_info=True)

    dashboard_url = common.dashboard_thread_url(thread_id)
    message = warning(
        "Open SWE hit an unexpected error while handling this Slack thread. "
        "Send another message and it will try again."
    )
    if dashboard_url:
        message += f" You can view the error in <{dashboard_url}|Open SWE Web>."
    try:
        await common.post_slack_thread_reply(
            channel_id, thread_ts, message, agent_thread_id=thread_id
        )
    except Exception:  # noqa: BLE001
        common.logger.warning(
            "Could not post Slack error notification for thread %s", thread_id, exc_info=True
        )


async def _process_slack_mention_impl(
    event_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    attachments = event_data.get("attachments", [])
    bot_user_id = event_data.get("bot_user_id", "")
    message_update = bool(event_data.get("message_update"))
    reply_thread_ts = event_data.get("reply_thread_ts")
    if not isinstance(reply_thread_ts, str):
        reply_thread_ts = ""
    original_message_ts = event_data.get("original_message_ts")
    if not isinstance(original_message_ts, str) or not original_message_ts:
        original_message_ts = event_ts
    channel_context_raw = event_data.get("channel_context")
    channel_context = (
        channel_context_raw
        if isinstance(channel_context_raw, dict)
        else common.normalize_slack_channel_context(channel_id, None)
    )

    if not channel_id or not thread_ts or not event_ts:
        common.logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    langgraph_client = get_langgraph_client()
    thread_id = event_data.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        thread_id = await common.resolve_slack_thread_id(langgraph_client, channel_id, thread_ts)
    # Prime the user-mapping cache so login/email/slack-id lookups below are warm.
    try:
        await common.refresh_user_mapping_cache()
    except Exception:  # noqa: BLE001
        common.logger.debug("Could not refresh user mapping cache for Slack mention", exc_info=True)

    user_email = None
    user_name = ""
    user_timezone = ""
    if user_id:
        slack_user = await common.get_slack_user_info(user_id)
        if slack_user:
            profile = slack_user.get("profile", {})
            if isinstance(profile, dict):
                user_email = profile.get("email")
                user_name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or slack_user.get("real_name")
                    or slack_user.get("name")
                    or ""
                )
            timezone_value = slack_user.get("tz")
            if isinstance(timezone_value, str):
                user_timezone = timezone_value.strip()

    context_thread_ts = reply_thread_ts or thread_ts
    thread_messages = (
        []
        if message_update
        else await common.fetch_slack_thread_messages(channel_id, context_thread_ts)
    )
    current_message = next(
        (message for message in thread_messages if str(message.get("ts")) == original_message_ts),
        None,
    )
    if current_message is None and not message_update:
        thread_messages.append(
            {
                "ts": event_ts,
                "text": text,
                "user": user_id,
                "attachments": attachments,
            }
        )
    elif current_message is not None and attachments and not current_message.get("attachments"):
        current_message["attachments"] = attachments

    treat_all_messages_as_mentions = bool(event_data.get("treat_all_messages_as_mentions"))
    untagged_reply = bool(event_data.get("untagged_reply"))
    # Interactions carry no flag of their own, and a location at the session
    # timestamp is a code channel whether or not the caller said so.
    code_channel = bool(event_data.get("code_channel")) or thread_ts == CODE_CHANNEL_SESSION_TS
    # A Slack event always has a channel, so this always resolves; the check is
    # the type system's, not a real case.
    surface = slack_surface(
        SlackThreadRef(
            channel_id=channel_id,
            thread_ts=thread_ts,
            surface="slack_channel" if code_channel else "slack_thread",
            reply_thread_ts=reply_thread_ts if code_channel else "",
        )
    )
    if surface is None:
        common.logger.warning("Slack event without a resolvable session: %s", channel_id)
        return
    context_messages, context_mode = common.select_slack_context_messages(
        thread_messages,
        event_ts,
        bot_user_id,
        common.SLACK_BOT_USERNAME,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions,
    )
    source_messages = (
        [{"ts": event_ts, "text": text, "user": user_id, "attachments": attachments}]
        if message_update
        else context_messages
    )
    context_user_ids = [
        value
        for value in (message.get("user") for message in context_messages)
        if isinstance(value, str) and value
    ]
    user_names_by_id = await common.get_slack_user_names(context_user_ids)
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    logins_by_user_id = await _slack_logins_by_user_id([*context_user_ids, user_id])
    context_source = "the beginning of the thread"
    if context_mode == "last_mention":
        context_source = (
            "the previous direct message"
            if treat_all_messages_as_mentions
            else "the previous message where I was tagged"
        )
    clean_text = (
        common.strip_bot_mention(text, bot_user_id, bot_username=common.SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    is_first_mention = not await common._thread_exists(thread_id)
    # `env:<name>` on the message that opens a thread picks the environment its
    # sandbox boots from. Only the opening message can: the sandbox is created
    # once, so honoring a later tag would change the prompt but not the image. The
    # tag is stripped only when it resolves, so a typo stays visible in the
    # transcript instead of vanishing.
    environment_slug: str | None = None
    if is_first_mention:
        tagged_slug, text_without_tag = parse_environment_tag(clean_text)
        if tagged_slug and await ENVIRONMENTS.get(tagged_slug) is not None:
            environment_slug = tagged_slug
            clean_text = text_without_tag or "(no text in mention)"
        elif tagged_slug:
            common.logger.info(
                "Slack thread %s tagged unknown environment %s; using the default",
                thread_id,
                tagged_slug,
            )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")
    trigger_user_timezone_section = (
        f"## Triggering User Time Zone\n{user_timezone}\n\n" if user_timezone else ""
    )

    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = await common.resolve_slack_links_in_context(
        source_messages, user_names_by_id
    )

    slack_thread_section = _format_slack_thread_section(
        channel_id, thread_ts, context_source, channel_context
    )
    operational_context = (
        _slack_prompt_preamble(untagged_reply, message_update) + "## Default Repository Hint\n"
        f"{repo_config.get('owner')}/{repo_config.get('name')}\n"
        "Use this only if the Slack conversation does not identify a different repository.\n\n"
        f"## Triggered by\n{trigger_user}\n\n"
        f"{trigger_user_timezone_section}"
        f"{slack_thread_section}\n\n"
        f"{await _format_slack_run_links_section(thread_id)}"
        + (f"\n\n{resolved_links_section}" if resolved_links_section else "")
        + (f"\n\n{surface_section}" if (surface_section := surface.prompt_section()) else "")
    )
    content_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(clean_text))]

    image_urls = common.dedupe_urls(
        [url for msg in source_messages for url in common.extract_image_urls(msg.get("text", ""))]
        + [
            f["url_private"]
            for msg in source_messages
            for f in msg.get("files", [])
            if isinstance(f, dict)
            and f.get("mimetype", "").startswith("image/")
            and f.get("url_private")
        ]
        + image_urls_from_links
    )

    mapped_login = await common.login_for_slack_id(user_id)
    if not mapped_login and user_email:
        mapped_login = await common.login_for_email(user_email)

    image_model_override: tuple[str, str] | None = None
    if image_urls:
        resolved_model_id = await common.resolve_agent_model_id(mapped_login)
        if not common.model_supports_images(resolved_model_id):
            fallback_model_id, fallback_effort = common.default_vision_model_pair()
            common.logger.info(
                "Using vision fallback model %s for %d Slack image(s); configured model %s "
                "does not support images",
                fallback_model_id,
                len(image_urls),
                resolved_model_id,
            )
            resolved_model_id = fallback_model_id
            image_model_override = (fallback_model_id, fallback_effort)
        common.logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx.AsyncClient(timeout=common.DEFAULT_HTTP_TIMEOUT) as http_client:
            for image_url in image_urls:
                image_block = await common.fetch_image_block(image_url, http_client)
                if image_block:
                    content_blocks.append(cast(dict[str, Any], image_block))

    # Open SWE opens PRs as the triggering user, so a run only proceeds when we
    # have a valid user GitHub token. Users who have never signed in with
    # GitHub, and users whose stored authorization is no longer usable, are
    # blocked and prompted to set up via the dashboard. Bot-token-only
    # deployments are exempt — they run on the installation token.
    user_token: str | None = None
    if mapped_login:
        try:
            user_token = await common.get_valid_access_token(mapped_login)
        except Exception:  # noqa: BLE001
            common.logger.debug(
                "Failed to resolve GitHub token for %s; treating as unauthenticated",
                mapped_login,
                exc_info=True,
            )
            user_token = None
    has_valid_user_token = bool(user_token)

    if not has_valid_user_token and not common.is_bot_token_only_mode():
        # A stored-but-unusable token means "sign in again"; no record at all
        # means the user has never connected GitHub + Slack via the dashboard.
        # Guard the store read like token resolution above so a transient
        # failure still yields an actionable prompt and clears the status.
        has_token_record = False
        if mapped_login:
            try:
                has_token_record = await common.has_access_token_record(mapped_login)
            except Exception:  # noqa: BLE001
                common.logger.debug(
                    "Failed to check GitHub token record for %s; prompting sign-in",
                    mapped_login,
                    exc_info=True,
                )
        reason = "revoked" if has_token_record else "unlinked"
        common.logger.info(
            "Blocking Slack run for thread %s: no valid user GitHub token (%s)",
            thread_id,
            reason,
        )
        if user_id:
            await common._post_account_link_prompt(
                channel_id,
                thread_ts,
                user_id,
                user_email,
                reason=reason,
                agent_thread_id=thread_id,
            )
        return

    slack_thread_context = SlackThreadRef(
        channel_id=channel_id,
        channel_context=channel_context,
        thread_ts=thread_ts,
        team_id=str(event_data.get("team_id") or ""),
        surface="slack_channel" if code_channel else "slack_thread",
        triggering_user_id=user_id,
        triggering_user_name=user_name,
        triggering_user_email=user_email,
        triggering_user_timezone=user_timezone,
        triggering_event_ts=event_ts,
        reply_thread_ts=reply_thread_ts if code_channel else "",
    ).dump()

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "slack_thread": slack_thread_context,
        "user_email": user_email,
        "source": "slack",
    }
    if mapped_login:
        configurable["github_login"] = mapped_login
        logins_by_user_id[user_id] = mapped_login
    # Later mentions carry no tag, so the thread's environment comes back from
    # metadata — a follow-up must not be told about `default` while its sandbox
    # was built from the environment the opening message picked.
    thread_environment = environment_slug or await common._get_thread_environment(thread_id)
    if thread_environment:
        configurable["environment"] = thread_environment
    if image_model_override:
        configurable["agent_model_id"] = image_model_override[0]
        configurable["agent_effort"] = image_model_override[1]

    thread_plan_mode = await common._get_thread_plan_mode(thread_id)
    if thread_plan_mode is not None:
        configurable["plan_mode"] = thread_plan_mode

    is_first_mention = not await common._thread_exists(thread_id)
    langgraph_client = get_langgraph_client()
    await common._upsert_slack_thread_repo_metadata(thread_id, repo_config, langgraph_client)
    # Pass the login resolved above (from the stable Slack user id) so the thread is
    # always tagged with github_login — the key the dashboard searches by. Without
    # it, upsert re-resolves from the Slack profile email, which can miss.
    await common.upsert_agent_thread_metadata(
        thread_id,
        source="slack",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=clean_text if is_first_mention else "",
        source_context=SourceContext.parse({"slack_thread": configurable["slack_thread"]}),
        environment=environment_slug,
    )

    # An edit corrects a request the agent already has, so it belongs in the
    # thread's message queue rather than in a run of its own. Nothing drains that
    # queue while the thread is idle; an edit made after the agent finished waits
    # for the next message.
    if message_update and await queue_message_for_thread(
        thread_id, [{"type": "text", "text": _MESSAGE_UPDATE_PREAMBLE.strip()}, *content_blocks]
    ):
        common.logger.info("Queued Slack message edit for thread %s", thread_id)
        return

    explicitly_tagged = _interrupts_active_run(
        text,
        bot_user_id,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions,
        code_channel=code_channel,
        message_update=message_update,
        explicit_request=bool(event_data.get("explicit_request")),
    )
    run_input = _slack_context_input(
        context_messages,
        user_names_by_id,
        logins_by_user_id,
        channel_id=channel_id,
        bot_user_id=bot_user_id,
        event_ts=event_ts,
        trigger_user_id=user_id,
        request_text=clean_text,
        request_blocks=content_blocks,
        operational_context=operational_context,
    )
    await surface.begin_turn()
    if is_first_mention:
        await surface.start_session(repo=repo_config, thread_id=thread_id)
    try:
        run = await _dispatch_or_queue_slack_run(
            langgraph_client,
            thread_id,
            run_input,
            configurable,
            explicitly_tagged=explicitly_tagged,
        )
    except Exception:
        # No run means no completion webhook, so nothing else would ever clear
        # the loading UI this turn switched on.
        await surface.end_turn()
        raise
    common.logger.info(
        "Slack LangGraph run %s dispatched for thread %s",
        common._run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if is_first_mention:
        if isinstance(run_id, str) and run_id:
            await common.store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts=original_message_ts,
                triggering_user_id=user_id,
                agent_thread_id=thread_id,
            )
    else:
        common.logger.info(
            "Skipping Slack trace reply for thread %s — agent will reply when run completes",
            thread_id,
        )
        if isinstance(run_id, str) and run_id:
            await common.store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts=original_message_ts,
                triggering_user_id=user_id,
                agent_thread_id=thread_id,
            )
