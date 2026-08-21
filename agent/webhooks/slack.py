"""Slack webhook handling: repo/channel resolution and the mention-triggered run."""

import logging
import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from fastapi import HTTPException
from langchain_core.messages.content import create_text_block
from langgraph_sdk.client import LangGraphClient

from ..config import (
    agent_version_metadata,
    langgraph_client,
    slack_bot_username,
    slack_repo_name,
    slack_repo_owner,
)
from ..dispatch import dispatch_agent_run
from ..github.auth import is_bot_token_only_mode
from ..github.refs import extract_repo_from_text
from ..input_messages import (
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
from ..langsmith.api import get_langsmith_trace_url
from ..settings.agent_overrides import resolve_agent_model_id
from ..settings.environments import get_environment, parse_environment_tag
from ..settings.github_tokens import get_valid_access_token, has_access_token_record
from ..settings.options import default_vision_model_pair, model_supports_images
from ..settings.team_settings import get_team_default_repo
from ..settings.user_mappings import login_for_email, login_for_slack_id
from ..settings.user_mappings import refresh_cache as refresh_user_mapping_cache
from ..slack.api import (
    fetch_slack_thread_messages,
    get_slack_channel_context,
    get_slack_channel_description,
    get_slack_channel_info,
    get_slack_user_info,
    get_slack_user_names,
    post_slack_thread_reply,
    resolve_slack_links_in_context,
)
from ..slack.format import (
    format_slack_messages_for_prompt,
    get_slack_channel_context_description,
    is_own_slack_message,
    is_slack_channel_named,
    normalize_slack_channel_context,
    parse_slack_ts,
    select_slack_context_messages,
    slack_message_bot_id,
    slack_message_bot_name,
    strip_bot_mention,
)
from ..slack.threads import (
    lookup_slack_thread_id,
    resolve_slack_thread_id,
    store_slack_run_mapping,
)
from ..utils.dashboard_links import build_settings_url, dashboard_thread_url
from ..utils.http import DEFAULT_HTTP_TIMEOUT
from ..utils.json_types import ThreadLike, as_json_object, as_thread_dict
from ..utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from ..utils.thread_ops import (
    fetch_thread_metadata,
    get_thread_environment,
    get_thread_plan_mode,
    is_not_found_error,
    thread_exists,
    upsert_agent_thread_owner_metadata,
)
from ..utils.user_messages import warning
from .repo_config import profile_default_repo_for_email

logger = logging.getLogger(__name__)

STALE_PARTICIPANT_SECONDS = 15 * 60
RAPID_FOLLOWUP_SECONDS = 60

DOCS_PLZ_SLACK_CHANNEL_NAME = "docs-plz"
DOCS_PLZ_SLACK_GATE_REPLY = (
    "Please don't use Open SWE here, instead ask the Fleet docs-plz agent to implement the docs"
)

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


async def resolve_slack_channel_context(channel_id: str) -> dict[str, str]:
    """Fetch Slack channel context without blocking Slack-triggered runs on failure."""
    try:
        return await get_slack_channel_context(channel_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to resolve Slack channel context")
        return normalize_slack_channel_context(channel_id, None)


async def is_docs_plz_slack_channel(
    channel_id: str, channel_context: dict[str, Any] | None = None
) -> bool:
    """Check whether a Slack channel is the docs-plz handoff channel."""
    if channel_context is not None:
        return is_slack_channel_named(channel_context, DOCS_PLZ_SLACK_CHANNEL_NAME)
    try:
        channel = await get_slack_channel_info(channel_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to resolve Slack channel info for docs-plz gate")
        return False
    return is_slack_channel_named(
        normalize_slack_channel_context(channel_id, channel), DOCS_PLZ_SLACK_CHANNEL_NAME
    )


def _extract_repo_config_from_thread(thread: ThreadLike) -> dict[str, str] | None:
    """Extract repo config from persisted thread data."""
    thread = as_thread_dict(thread)
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        return None

    repo = metadata.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}

    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return {"owner": owner, "name": name}

    return None


async def get_slack_repo_config(
    channel_id: str,
    thread_ts: str,
    slack_user_id: str | None = None,
    channel_context: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> dict[str, str]:
    """Resolve repository configuration for Slack-triggered runs.

    Priority:
        1. Repo carried over from the existing Slack thread's metadata.
        2. A ``repo:owner/name`` token in the channel's topic/purpose.
        3. The triggering user's dashboard ``default_repo`` (if they have a
           profile and their Slack email maps to a known GitHub login).
        4. Team default repo.
        5. ``SLACK_REPO_*`` env defaults.
    """
    default_owner = slack_repo_owner()
    default_name = slack_repo_name()
    client = langgraph_client()

    repo_config: dict[str, str] | None = None

    try:
        resolved_thread_id = thread_id or await resolve_slack_thread_id(
            client, channel_id, thread_ts
        )
        thread = await client.threads.get(resolved_thread_id)
        thread_repo_config = _extract_repo_config_from_thread(thread)
        if thread_repo_config:
            repo_config = thread_repo_config
    except Exception as exc:  # noqa: BLE001
        if not is_not_found_error(exc):
            logger.debug("Failed to fetch Slack thread %s for repo resolution", thread_id)

    if not repo_config:
        try:
            if channel_context is not None:
                channel_description = get_slack_channel_context_description(channel_context)
            else:
                channel_description = await get_slack_channel_description(channel_id)
            if channel_description:
                channel_repo_config = extract_repo_from_text(
                    channel_description, default_owner=default_owner
                )
                if channel_repo_config:
                    logger.info(
                        "Applying repo from Slack channel %s description: %s/%s",
                        channel_id,
                        channel_repo_config["owner"],
                        channel_repo_config["name"],
                    )
                    repo_config = channel_repo_config
        except Exception:  # noqa: BLE001
            logger.exception("Failed to resolve repo from Slack channel description")

    if not repo_config and slack_user_id:
        slack_user = await get_slack_user_info(slack_user_id)
        slack_email = (
            (slack_user or {}).get("profile", {}).get("email")
            if isinstance(slack_user, dict)
            else None
        )
        repo_config = await profile_default_repo_for_email(slack_email, channel="Slack")

    if not repo_config:
        repo_config = await get_team_default_repo()

    if not repo_config and default_owner and default_name:
        repo_config = {"owner": default_owner, "name": default_name}

    if not repo_config:
        raise HTTPException(400, "no default repository configured")

    return repo_config


async def upsert_slack_thread_repo_metadata(
    thread_id: str, repo_config: dict[str, str], client: LangGraphClient
) -> None:
    """Persist the selected repo config on the thread metadata."""
    try:
        await client.threads.update(thread_id=thread_id, metadata={"repo": repo_config})
    except Exception as exc:  # noqa: BLE001
        if is_not_found_error(exc):
            try:
                await client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"repo": repo_config},
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to create Slack thread %s while persisting repo metadata",
                    thread_id,
                )
            return
        logger.exception(
            "Failed to persist Slack thread repo metadata for thread %s",
            thread_id,
        )


async def slack_user_is_thread_owner(thread_id: str, slack_user_id: str) -> bool:
    """Whether the clicking Slack user is the user who requested the plan.

    Plan approval is owner-only (mirrors the dashboard plan API's
    ``_user_owns_thread`` gate). The original requester's Slack id is stored in
    ``source_context.slack_thread.triggering_user_id`` when the run is created.
    Fails closed when ownership can't be determined.
    """
    if not slack_user_id:
        return False
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        return False
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    if not isinstance(metadata, dict):
        return False
    source_context = metadata.get("source_context")
    slack_thread = source_context.get("slack_thread") if isinstance(source_context, dict) else None
    owner_id = slack_thread.get("triggering_user_id") if isinstance(slack_thread, dict) else None
    return isinstance(owner_id, str) and bool(owner_id) and owner_id == slack_user_id


async def post_account_link_prompt(
    channel_id: str,
    thread_ts: str,
    user_id: str,
    user_email: str | None,
    reason: str = "unlinked",
    agent_thread_id: str | None = None,
) -> None:
    """Prompt a Slack user to connect their account via the dashboard.

    ``reason`` is ``"unlinked"`` (never signed in with GitHub) or ``"revoked"``
    (signed in before, but the stored GitHub authorization is no longer usable).
    Open SWE opens PRs as the triggering user, so it cannot start until the user
    has signed in with GitHub and connected their Slack account in the dashboard.

    Posts a plain, token-free dashboard link as a visible threaded reply. The
    link carries no per-user identity, so it's safe to show in a shared channel:
    the user signs in with GitHub from their own session and connects Slack via
    verified OIDC on the settings page.
    """
    settings_url = build_settings_url()
    if not settings_url:
        logger.debug(
            "Dashboard settings URL unavailable (DASHBOARD_BASE_URL unset); skipping prompt"
        )
        return
    if reason == "revoked":
        text = (
            "🔐 Your GitHub sign-in is no longer valid, so I can't resolve your GitHub "
            f"account. Re-connect it in <{settings_url}|your Open SWE settings>, then tag me again."
        )
    else:
        text = (
            "👋 I couldn't resolve your GitHub account from Slack. Sign in with GitHub and "
            f"connect your Slack account in <{settings_url}|your Open SWE settings>, then tag me "
            "again."
        )
    try:
        await post_slack_thread_reply(channel_id, thread_ts, text, agent_thread_id=agent_thread_id)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to post account-link prompt to Slack", exc_info=True)


def _run_id_for_logging(run: Any) -> str:
    """Extract a run id from SDK response shapes for log messages."""
    if isinstance(run, dict):
        run_id = run.get("run_id")
    else:
        run_id = getattr(run, "run_id", None)
    return run_id if isinstance(run_id, str) and run_id else "<unknown>"


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
        or bool((username := slack_bot_username()) and f"@{username}" in text)
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

    messages = await fetch_slack_thread_messages(channel_id, thread_ts)
    bot_last_ts = 0.0
    current_ts = parse_slack_ts(now_ts)
    latest_ts = current_ts
    last_message_ts: dict[str, float] = {}
    human_messages: list[tuple[float, str, str]] = []
    for message in messages:
        message_ts = parse_slack_ts(message.get("ts"))
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
        if bot_username := slack_bot_username():
            mention_tokens.append(f"@{bot_username}")
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
        await dispatch_agent_run(
            thread_id,
            None,
            configurable,
            source="slack",
            input=run_input,
            metadata=agent_version_metadata(),
            client=client,
            multitask_strategy="interrupt" if explicitly_tagged else "enqueue",
        )
    )


async def _slack_user_can_reply_to_ready_plan(
    channel_id: str, thread_ts: str, slack_user_id: str
) -> bool:
    if not channel_id or not thread_ts or not slack_user_id:
        return False
    try:
        thread_id = await lookup_slack_thread_id(langgraph_client(), channel_id, thread_ts)
    except Exception:  # noqa: BLE001
        return False
    if not thread_id:
        return False
    # A brand-new thread has no metadata; an untagged message there simply
    # isn't a plan reply.
    metadata = await fetch_thread_metadata(thread_id)
    if metadata is None:
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
    channel_description = get_slack_channel_context_description(channel_context)
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


_OPEN_SWE_SENDER_ID = "system:open-swe"


async def _slack_logins_by_user_id(user_ids: list[str]) -> dict[str, str]:
    """Map Slack user ids to the GitHub logins of linked Open SWE accounts."""
    logins: dict[str, str] = {}
    for user_id in {value for value in user_ids if value}:
        login = await login_for_slack_id(user_id)
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
    if is_own_slack_message(message, bot_user_id):
        identity: SystemIdentity = {
            "id": _OPEN_SWE_SENDER_ID,
            "display_name": slack_bot_username() or "Open SWE",
            "platform": "slack",
            "sender_type": "self",
        }
        return identity["id"], identity, "system"
    bot_id = slack_message_bot_id(message)
    if bot_id:
        bot: SystemIdentity = {
            "id": f"system:slack-bot-{bot_id}",
            "display_name": slack_message_bot_name(message),
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
    forwarded = format_slack_messages_for_prompt(
        [message], {}, bot_user_id=bot_user_id, bot_username=slack_bot_username()
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
    trigger_id = next(
        (
            str(message.get("user"))
            for message in messages
            if str(message.get("ts", "")) == str(event_ts) and message.get("user")
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
        logger.exception("Unexpected error while processing Slack mention")
        await _notify_slack_processing_error(event_data, repo_config)


async def process_slack_plan_approval(
    event_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    from ..dashboard.plan_api import approve_plan_for_thread
    from ..settings.plan_store import make_plan_approver

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
        logger.exception("Unexpected error while processing Slack plan approval")
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
            thread_id = await lookup_slack_thread_id(langgraph_client(), channel_id, thread_ts)
        except Exception:  # noqa: BLE001
            thread_id = None
    if not thread_id:
        return
    try:
        clean_text = (
            strip_bot_mention(text, bot_user_id, bot_username=slack_bot_username())
            or "Slack request"
        )
        await upsert_agent_thread_owner_metadata(
            thread_id,
            source="slack",
            repo_config=repo_config,
            title=clean_text,
            source_context={
                "slack_thread": {
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "triggering_user_id": user_id,
                    "triggering_event_ts": event_ts,
                }
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not persist Slack error metadata for thread %s", thread_id, exc_info=True
        )

    try:
        await langgraph_client().threads.update(
            thread_id=thread_id,
            metadata={
                "latest_run_status": "error",
                "updated_at_ms": int(datetime.now(UTC).timestamp() * 1000),
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not mark Slack thread %s as errored", thread_id, exc_info=True)

    dashboard_url = dashboard_thread_url(thread_id)
    message = warning(
        "Open SWE hit an unexpected error while handling this Slack thread. "
        "Send another message and it will try again."
    )
    if dashboard_url:
        message += f" You can view the error in <{dashboard_url}|Open SWE Web>."
    try:
        await post_slack_thread_reply(channel_id, thread_ts, message, agent_thread_id=thread_id)
    except Exception:  # noqa: BLE001
        logger.warning(
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
    original_message_ts = event_data.get("original_message_ts")
    if not isinstance(original_message_ts, str) or not original_message_ts:
        original_message_ts = event_ts
    channel_context_raw = event_data.get("channel_context")
    channel_context = (
        channel_context_raw
        if isinstance(channel_context_raw, dict)
        else normalize_slack_channel_context(channel_id, None)
    )

    if not channel_id or not thread_ts or not event_ts:
        logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    client = langgraph_client()
    thread_id = event_data.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        thread_id = await resolve_slack_thread_id(client, channel_id, thread_ts)

    # Prime the user-mapping cache so login/email/slack-id lookups below are warm.
    try:
        await refresh_user_mapping_cache()
    except Exception:  # noqa: BLE001
        logger.debug("Could not refresh user mapping cache for Slack mention", exc_info=True)

    user_email = None
    user_name = ""
    user_timezone = ""
    if user_id:
        slack_user = await get_slack_user_info(user_id)
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

    thread_messages = (
        [] if message_update else await fetch_slack_thread_messages(channel_id, thread_ts)
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
    context_messages, context_mode = select_slack_context_messages(
        thread_messages,
        event_ts,
        bot_user_id,
        slack_bot_username(),
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
    user_names_by_id = await get_slack_user_names(context_user_ids)
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
        strip_bot_mention(text, bot_user_id, bot_username=slack_bot_username())
        or "(no text in mention)"
    )
    is_first_mention = not await thread_exists(thread_id)
    # `env:<name>` on the message that opens a thread picks the environment its
    # sandbox boots from. Only the opening message can: the sandbox is created
    # once, so honoring a later tag would change the prompt but not the image. The
    # tag is stripped only when it resolves, so a typo stays visible in the
    # transcript instead of vanishing.
    environment_slug: str | None = None
    if is_first_mention:
        tagged_slug, text_without_tag = parse_environment_tag(clean_text)
        if tagged_slug and await get_environment(tagged_slug) is not None:
            environment_slug = tagged_slug
            clean_text = text_without_tag or "(no text in mention)"
        elif tagged_slug:
            logger.info(
                "Slack thread %s tagged unknown environment %s; using the default",
                thread_id,
                tagged_slug,
            )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")
    trigger_user_timezone_section = (
        f"## Triggering User Time Zone\n{user_timezone}\n\n" if user_timezone else ""
    )

    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = await resolve_slack_links_in_context(
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
    )
    content_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(clean_text))]

    image_urls = dedupe_urls(
        [url for msg in source_messages for url in extract_image_urls(msg.get("text", ""))]
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

    mapped_login = await login_for_slack_id(user_id)
    if not mapped_login and user_email:
        mapped_login = await login_for_email(user_email)

    image_model_override: tuple[str, str] | None = None
    if image_urls:
        resolved_model_id = await resolve_agent_model_id(mapped_login)
        if not model_supports_images(resolved_model_id):
            fallback_model_id, fallback_effort = default_vision_model_pair()
            logger.info(
                "Using vision fallback model %s for %d Slack image(s); configured model %s "
                "does not support images",
                fallback_model_id,
                len(image_urls),
                resolved_model_id,
            )
            resolved_model_id = fallback_model_id
            image_model_override = (fallback_model_id, fallback_effort)
        logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, http_client)
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
            user_token = await get_valid_access_token(mapped_login)
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to resolve GitHub token for %s; treating as unauthenticated",
                mapped_login,
                exc_info=True,
            )
            user_token = None
    has_valid_user_token = bool(user_token)

    if not has_valid_user_token and not is_bot_token_only_mode():
        # A stored-but-unusable token means "sign in again"; no record at all
        # means the user has never connected GitHub + Slack via the dashboard.
        # Guard the store read like token resolution above so a transient
        # failure still yields an actionable prompt and clears the status.
        has_token_record = False
        if mapped_login:
            try:
                has_token_record = await has_access_token_record(mapped_login)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Failed to check GitHub token record for %s; prompting sign-in",
                    mapped_login,
                    exc_info=True,
                )
        reason = "revoked" if has_token_record else "unlinked"
        logger.info(
            "Blocking Slack run for thread %s: no valid user GitHub token (%s)",
            thread_id,
            reason,
        )
        if user_id:
            await post_account_link_prompt(
                channel_id,
                thread_ts,
                user_id,
                user_email,
                reason=reason,
                agent_thread_id=thread_id,
            )
        return

    slack_thread_context: dict[str, Any] = {
        "channel_id": channel_id,
        "channel_context": channel_context,
        "thread_ts": thread_ts,
        "triggering_user_id": user_id,
        "triggering_user_name": user_name,
        "triggering_user_email": user_email,
        "triggering_event_ts": event_ts,
    }
    if user_timezone:
        slack_thread_context["triggering_user_timezone"] = user_timezone

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
    thread_environment = environment_slug or await get_thread_environment(thread_id)
    if thread_environment:
        configurable["environment"] = thread_environment
    if image_model_override:
        configurable["agent_model_id"] = image_model_override[0]
        configurable["agent_effort"] = image_model_override[1]

    thread_plan_mode = await get_thread_plan_mode(thread_id)
    if thread_plan_mode is not None:
        configurable["plan_mode"] = thread_plan_mode

    is_first_mention = not await thread_exists(thread_id)
    client = langgraph_client()
    await upsert_slack_thread_repo_metadata(thread_id, repo_config, client)
    # Pass the login resolved above (from the stable Slack user id) so the thread is
    # always tagged with github_login — the key the dashboard searches by. Without
    # it, upsert re-resolves from the Slack profile email, which can miss.
    await upsert_agent_thread_owner_metadata(
        thread_id,
        source="slack",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=clean_text if is_first_mention else "",
        source_context={"slack_thread": configurable["slack_thread"]},
        environment=environment_slug,
    )

    # A DM (treat_all_messages_as_mentions) is inherently directed at the bot, so
    # it interrupts immediately like an explicit @-mention rather than debouncing.
    explicitly_tagged = _is_explicit_slack_request(
        text,
        bot_user_id,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions,
        message_update=message_update,
    )
    run_input = _slack_context_input(
        context_messages,
        user_names_by_id,
        logins_by_user_id,
        channel_id=channel_id,
        bot_user_id=bot_user_id,
        event_ts=event_ts,
        request_text=clean_text,
        request_blocks=content_blocks,
        operational_context=operational_context,
    )
    run = await _dispatch_or_queue_slack_run(
        client,
        thread_id,
        run_input,
        configurable,
        explicitly_tagged=explicitly_tagged,
    )
    logger.info(
        "Slack LangGraph run %s dispatched for thread %s",
        _run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if not is_first_mention:
        logger.info(
            "Skipping Slack trace reply for thread %s — agent will reply when run completes",
            thread_id,
        )
    if isinstance(run_id, str) and run_id:
        await store_slack_run_mapping(
            client,
            channel_id,
            thread_ts,
            run_id,
            message_ts=original_message_ts,
            triggering_user_id=user_id,
            agent_thread_id=thread_id,
        )
