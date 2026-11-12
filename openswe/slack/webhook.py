"""Slack webhook handler — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
from fastapi import HTTPException
from langchain_core.messages.content import create_text_block
from pydantic import JsonValue
from runtime_api.slack import SlackNotice, SlackTurnPrompt

from openswe.dashboard.langsmith_login import LangSmithLogin
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.human_review.requests import HumanReviewRequest
from openswe.input_messages import (
    ChannelIdentity,
    InputMessageContext,
    MessageKind,
    PersonIdentity,
    RunInput,
    RunMessage,
    SystemIdentity,
    channel_introduction,
    dynamic_context_hash,
    human_input,
    input_message_timestamps,
    person_introduction,
    system_input,
    system_introduction,
    visible_dynamic_context_hashes,
)
from openswe.model_routing import RoutingArm
from openswe.prompts import prompt
from openswe.run_config import Repo
from openswe.slack import client as slack_utils
from openswe.slack.allowed_bots import AllowedSlackBot, resolve_allowed_slack_bot
from openswe.slack.channels import SlackChannel
from openswe.slack.dm import DmOrigin, dm_thread_title, is_concierge_thread, is_dm_channel
from openswe.slack.failures import report_slack_failure
from openswe.slack.parsed_message import ParsedSlackMessage
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.request import SlackRequest
from openswe.slack.staged_files import (
    queue_slack_files,
    slack_file_entries,
    slack_files_section,
)
from openswe.slack.thinking import (
    clear_slack_thinking_status_if_idle,
    restore_slack_thinking_status,
    show_slack_thinking_status,
)
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.threads.identity import ThreadIdentity
from openswe.threads.store import THREADS
from openswe.users import User, persist_display_name
from openswe.utils.http_pool import pooled_transport
from openswe.utils.json_types import as_json_object
from openswe.utils.langsmith import create_langsmith_feedback, get_langsmith_trace_url
from openswe.utils.thread_ops import (
    langgraph_client as get_langgraph_client,
)
from openswe.utils.thread_ops import queue_message_for_thread
from openswe.utils.thread_participants import slack_participant_ids
from openswe.utils.thread_settings import load_thread_settings
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace, workspace_for_repo
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

_CONCIERGE_CONTEXT = prompt("runs/slack-concierge")
_KITCHEN_CONTEXT = prompt("runs/slack-kitchen")
_NON_KITCHEN_CONTEXT = prompt("runs/slack-non-kitchen")
_MESSAGE_UPDATE_PREAMBLE = prompt("runs/slack-message-update")


def _is_explicit_slack_request(
    text: str,
    bot_user_id: str,
    *,
    treat_all_messages_as_mentions: bool,
    message_update: bool,
) -> bool:
    return not message_update and bool(
        treat_all_messages_as_mentions or (bot_user_id and f"<@{bot_user_id}>" in text)
    )


def _interrupts_active_run(
    text: str,
    bot_user_id: str,
    *,
    treat_all_messages_as_mentions: bool,
    message_update: bool,
    explicit_request: bool,
) -> bool:
    return explicit_request or _is_explicit_slack_request(
        text,
        bot_user_id,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions,
        message_update=message_update,
    )


async def _dispatch_or_queue_slack_run(
    client: Any,
    thread_id: str,
    run_input: RunInput | list[dict[str, Any]],
    configurable: dict[str, Any],
    *,
    interrupt_active_run: bool,
    trigger_ts: str,
    concierge_message_ts: str = "",
) -> dict[str, Any]:
    """Dispatch Slack turns using their channel's interruption policy."""
    if isinstance(run_input, list):
        run_input = {"messages": cast(list[Any], run_input)}
    return as_json_object(
        await common.dispatch_agent_run(
            thread_id,
            None,
            configurable,
            source="web" if configurable.get("source") == "web" else "slack",
            thread_title=None,
            input=run_input,
            metadata={
                **common.AGENT_VERSION_METADATA,
                **({} if configurable.get("source") == "web" else {"slack_trigger_ts": trigger_ts}),
                "slack_concierge_message_ts": concierge_message_ts,
            },
            client=client,
            multitask_strategy="interrupt" if interrupt_active_run else "enqueue",
        )
    )


async def _slack_channel_identity(
    channel_id: str,
    thread_ts: str,
    channel_context: SlackChannelContext,
    *,
    thread_id: str,
    repo: Repo | None,
) -> ChannelIdentity:
    """Everything that stays true of this Slack thread, for its context block."""
    channel: ChannelIdentity = {"id": f"slack:{channel_id}", "platform": "slack"}
    channel_name = channel_context.name_normalized.strip() or channel_context.name.strip()
    if channel_name:
        channel["name"] = f"#{channel_name}"
    if thread_ts:
        channel["thread_id"] = thread_ts
    topic, purpose = channel_context.topic.strip(), channel_context.purpose.strip()
    if topic:
        channel["topic"] = topic
    if purpose:
        channel["purpose"] = purpose
    # Slack's own `description` is usually the topic and purpose run together.
    if not topic and not purpose and channel_context.description.strip():
        channel["description"] = channel_context.description.strip()
    if repo is not None:
        channel["default_repo"] = repo.full_name
    dashboard_url = common.dashboard_thread_url(thread_id)
    if dashboard_url:
        channel["web_url"] = dashboard_url
    trace_url = await get_langsmith_trace_url(thread_id)
    if trace_url:
        channel["trace_url"] = trace_url
    return channel


def held_context_thread(request: SlackRequest, thread_id: str, *, exists: bool) -> str | None:
    """The thread whose Slack context the run's thread already has: a branch's
    conversation thread, which its new thread starts with, or the run's own thread once
    it exists; ``None`` for a new thread that starts with nothing."""
    if request.held_thread_id:
        return request.held_thread_id
    return thread_id if exists else None


async def _dispatched_slack_context(thread_id: str) -> tuple[set[str], set[str]]:
    """What the thread already holds: visible context hashes and replayed Slack ts.

    Both keep dispatch from re-sending what the model has. A thread whose state
    cannot be read is treated as empty, which repeats context rather than losing
    the turn.
    """
    try:
        state = as_json_object(await THREADS.state(thread_id))
    except Exception:  # noqa: BLE001
        common.logger.warning(
            "Could not read thread state; Slack context may repeat",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return set(), set()
    values = state.get("values")
    if not isinstance(values, dict):
        return set(), set()
    messages = values.get("messages")
    if not isinstance(messages, list):
        return set(), set()
    timestamps: set[str] = set()
    for message in messages:
        if isinstance(message, dict):
            timestamps |= input_message_timestamps(message.get("content"))
    return visible_dynamic_context_hashes(values), timestamps


_SLACK_CONTEXT_SENDER_ID = "system:slack-context"
_SLACK_NOTICE_SENDER: SystemIdentity = {
    "id": "system:slack-notice",
    "display_name": "Robin notices",
    "platform": "slack",
    "content": (
        "Notices Robin posts in the Slack thread itself, not an agent: for example, "
        "where a `/breakout` continues, or that a run failed."
    ),
}


async def _slack_accounts_by_user_id(
    user_ids: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """GitHub logins and person entity ids of the linked Open SWE accounts behind Slack users."""
    users = await User.for_identities("slack", user_ids)
    logins = {user_id: user.github_login for user_id, user in users.items() if user.github_login}
    person_ids = {user_id: f"user:{user.id}" for user_id, user in users.items()}
    return logins, person_ids


def _slack_person(
    user_id: str, name: str = "", github_login: str = "", person_id: str = ""
) -> PersonIdentity:
    person: PersonIdentity = {
        "id": person_id or f"slack:{user_id}",
        "open_swe_account": "linked" if person_id or github_login else "unlinked",
    }
    if name:
        person["display_name"] = name
    if github_login:
        person["github_login"] = github_login
    return person


def _slack_bot_system_identity(bot_id: str, name: str) -> SystemIdentity:
    return {
        "id": f"system:slack-bot-{bot_id}",
        "display_name": name,
        "platform": "slack",
        "sender_type": "bot",
    }


def _slack_bot_identity(bot: AllowedSlackBot) -> SystemIdentity:
    return _slack_bot_system_identity(bot.bot_id, bot.name.strip() or "Bot")


def _slack_sender(
    message: dict[str, Any],
    user_names_by_id: dict[str, str],
    logins_by_user_id: dict[str, str],
    person_ids_by_user_id: dict[str, str] | None = None,
) -> tuple[str, PersonIdentity | SystemIdentity, MessageKind]:
    """Resolve a thread message to its sender id, identity, and message kind.

    Open SWE's own replies never reach here — the model wrote them and already has
    them — so a message carrying a ``bot_id`` is somebody else's app.
    """
    bot_id = slack_utils.slack_message_bot_id(message)
    if bot_id:
        bot = _slack_bot_system_identity(bot_id, slack_utils.slack_message_bot_name(message))
        return bot["id"], bot, "system"
    user_id = str(message.get("user"))
    person = _slack_person(
        user_id,
        user_names_by_id.get(user_id, ""),
        logins_by_user_id.get(user_id, ""),
        (person_ids_by_user_id or {}).get(user_id, ""),
    )
    return person["id"], person, "human"


def _mentions_open_swe(text: object, bot_user_id: str) -> bool:
    return isinstance(text, str) and bool(
        (bot_user_id and f"<@{bot_user_id}>" in text)
        or (common.SLACK_BOT_USERNAME and f"@{common.SLACK_BOT_USERNAME}" in text)
    )


def _label_slack_mentions(
    text: str, user_names_by_id: dict[str, str], channel_names_by_id: dict[str, str]
) -> str:
    return slack_utils.label_slack_channel_mentions(
        slack_utils.label_slack_user_mentions(text, user_names_by_id), channel_names_by_id
    )


async def _public_slack_channel_names(channel_ids: list[str]) -> dict[str, str]:
    """Names of the public channels among `channel_ids`; a private name never leaves Slack."""
    unique_ids = sorted(set(channel_ids))
    channels = await asyncio.gather(*(SlackChannel.load(channel_id) for channel_id in unique_ids))
    return {
        channel.id: channel.details.name
        for channel in channels
        if channel is not None and channel.public and channel.details.name
    }


def _slack_message_text(
    message: dict[str, Any],
    bot_user_id: str,
    user_names_by_id: dict[str, str],
    channel_names_by_id: dict[str, str],
) -> str:
    forwarded = common.format_slack_messages_for_prompt(
        [message], {}, bot_user_id=bot_user_id, bot_username=common.SLACK_BOT_USERNAME
    )
    _, separator, content = forwarded.partition(": ")
    return _label_slack_mentions(
        content if separator else forwarded, user_names_by_id, channel_names_by_id
    )


def _slack_context_input(
    messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str],
    logins_by_user_id: dict[str, str],
    *,
    person_ids_by_user_id: dict[str, str] | None = None,
    channel_names_by_id: dict[str, str] | None = None,
    channel: ChannelIdentity,
    bot_user_id: str,
    event_ts: str,
    source_message_ts: str = "",
    context_channel_id: str = "",
    trigger_user_id: str = "",
    request_text: str,
    request_blocks: list[dict[str, Any]],
    prior_message_text: str = "",
    turn_context: str = "",
    constant_context: str = "",
    dispatched_timestamps: set[str] | None = None,
    run_described_person_ids: set[str] | None = None,
    visible_context_hashes: set[str] | None = None,
    trigger_system: SystemIdentity | None = None,
    explicit_mention: bool = False,
    web_only: bool = False,
) -> RunInput:
    channel_entity_id = channel["id"]
    link_source = not context_channel_id or context_channel_id == channel_entity_id.removeprefix(
        "slack:"
    )
    channel_names = channel_names_by_id or {}
    already_dispatched = dispatched_timestamps or set()
    visible = set(visible_context_hashes or ())
    run_messages: list[RunMessage] = []

    def add_context(message: RunMessage) -> None:
        """Append a context block unless the model can already see that content."""
        context_hash = dynamic_context_hash(message["content"])
        if context_hash is not None:
            if context_hash in visible:
                return
            visible.add(context_hash)
        run_messages.append(message)

    add_context(channel_introduction(channel))
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
        (person_ids_by_user_id or {}).get(trigger_id, ""),
    )
    # The run describes the trigger sender and every other person it knows, so
    # dispatch introduces only a replayed author nobody else will account for.
    described: set[str] = {trigger_person["id"], *(run_described_person_ids or ())}
    for message in messages:
        timestamp = str(message.get("ts", ""))
        # The model has every message an earlier dispatch handed it.
        if timestamp == str(event_ts) or timestamp in already_dispatched:
            continue
        sender_id: str
        identity: PersonIdentity | SystemIdentity
        kind: MessageKind
        if not web_only and slack_utils.is_own_slack_message(message, bot_user_id):
            # The model wrote its own replies and already has them; a notice it never saw.
            if not SlackNotice.marks(message):
                continue
            sender_id, identity, kind = _SLACK_NOTICE_SENDER["id"], _SLACK_NOTICE_SENDER, "system"
        else:
            sender_id, identity, kind = _slack_sender(
                message, user_names_by_id, logins_by_user_id, person_ids_by_user_id
            )
        if sender_id not in described:
            described.add(sender_id)
            add_context(
                person_introduction(cast(PersonIdentity, identity))
                if kind == "human"
                else system_introduction(cast(SystemIdentity, identity))
            )
        message_context: InputMessageContext = {
            "sender_id": sender_id,
            "channel_id": channel_entity_id,
            "surface": "slack",
            "kind": kind,
            "data": {
                "timestamp": timestamp,
                **({"slack_message_ts": timestamp} if link_source else {}),
                "explicit_bot_mention": str(
                    _mentions_open_swe(message.get("text"), bot_user_id)
                ).lower(),
            },
        }
        text = _slack_message_text(message, bot_user_id, user_names_by_id, channel_names)
        run_messages.append(
            human_input(text, message_context)
            if kind == "human"
            else system_input(text, message_context)
        )
    if prior_message_text:
        run_messages.append(
            human_input(
                _label_slack_mentions(prior_message_text, user_names_by_id, channel_names),
                {
                    "sender_id": trigger_person["id"],
                    "channel_id": channel_entity_id,
                    "surface": "slack",
                    "kind": "human",
                    "data": {
                        "timestamp": event_ts,
                        **(
                            {"slack_message_ts": source_message_ts}
                            if source_message_ts and link_source
                            else {}
                        ),
                        "explicit_bot_mention": str(
                            _mentions_open_swe(prior_message_text, bot_user_id)
                        ).lower(),
                    },
                },
            )
        )
    if constant_context or turn_context:
        slack_context: SystemIdentity = {
            "id": _SLACK_CONTEXT_SENDER_ID,
            "display_name": "Slack context",
            "platform": "slack",
        }
        if constant_context:
            slack_context["content"] = constant_context
        add_context(system_introduction(slack_context))
    if turn_context:
        run_messages.append(
            system_input(
                turn_context,
                {
                    "sender_id": _SLACK_CONTEXT_SENDER_ID,
                    "channel_id": channel_entity_id,
                    "surface": "slack",
                    "kind": "system",
                },
            )
        )
    trigger_sender_id = trigger_person["id"]
    trigger_kind: MessageKind = "human"
    if trigger_system is not None:
        trigger_sender_id, trigger_kind = trigger_system["id"], "system"
        if trigger_sender_id not in described:
            described.add(trigger_sender_id)
            add_context(system_introduction(trigger_system))
    current_message = next(
        (message for message in messages if str(message.get("ts", "")) == str(event_ts)), {}
    )
    forwarded_context = _label_slack_mentions(
        slack_utils.format_forwarded_slack_attachments(current_message.get("attachments")),
        user_names_by_id,
        channel_names,
    )
    if forwarded_context:
        request_text = f"{request_text}\n{forwarded_context}"
    request_blocks[0] = {**request_blocks[0], "text": request_text}
    run_messages.append(
        (system_input if trigger_system is not None else human_input)(
            request_blocks,
            {
                "sender_id": trigger_sender_id,
                "channel_id": channel_entity_id,
                "surface": "web" if web_only else "slack",
                "kind": trigger_kind,
                "data": {
                    "timestamp": event_ts,
                    **(
                        {"slack_message_ts": source_message_ts}
                        if source_message_ts and link_source
                        else {}
                    ),
                    "explicit_bot_mention": str(explicit_mention).lower(),
                },
            },
        )
    )
    return {"messages": run_messages}


async def _clear_early_status_if_idle(request: SlackRequest, status_ts: str) -> None:
    thread_id = request.thread_id
    if not thread_id:
        try:
            thread_id = await common.lookup_slack_thread_id(request.channel_id, request.thread_ts)
        except Exception:  # noqa: BLE001
            common.logger.warning("Could not determine whether Slack thread status is still owned")
            return
    if thread_id:
        await clear_slack_thinking_status_if_idle(thread_id, request.channel_id, status_ts)
    else:
        await slack_utils.set_slack_thread_status(request.channel_id, status_ts, "")


async def process_slack_mention(
    request: SlackRequest,
    repo: common.SlackRepoResolution | None,
    *,
    inherited_workspace: str | None = None,
) -> None:
    """Process a Slack request by creating a run or queuing a mid-run message."""
    status_ts = request.thread_ts
    show_status = bool(
        request.channel_id
        and status_ts
        and not request.concierge_mode
        and not request.message_update
    )
    # Showing the status shouldn't hold up the run, but it has to land before the
    # run's own status updates or a clear, or it would overwrite them.
    status_shown = (
        asyncio.ensure_future(restore_slack_thinking_status(request.channel_id, status_ts))
        if show_status
        else None
    )
    try:
        status_handed_off = await _process_slack_mention_impl(
            request, repo, inherited_workspace=inherited_workspace, status_shown=status_shown
        )
        if status_shown is not None and not status_handed_off:
            await asyncio.wait({status_shown})
            await _clear_early_status_if_idle(request, status_ts)
    except Exception as exc:  # noqa: BLE001
        if status_shown is not None:
            await asyncio.wait({status_shown})
            await _clear_early_status_if_idle(request, status_ts)
        await notify_slack_processing_error(request, repo.repo if repo else None, exc)


async def start_slack_run(request: SlackRequest, repo: common.SlackRepoResolution | None) -> bool:
    """Process ``request`` without the error reply ``process_slack_mention`` posts; whether a run started."""
    return await _process_slack_mention_impl(request, repo)


async def notify_slack_processing_error(
    request: SlackRequest, repo: Repo | None, exc: BaseException
) -> None:
    """Mark the agent thread errored when one exists, then always tell the Slack thread."""
    if request.concierge_mode:
        receipt_ts = request.original_message_ts or request.event_ts
        await slack_utils.remove_slack_reaction(
            request.channel_id, receipt_ts, "hourglass_flowing_sand"
        )
    thread_id = request.thread_id
    if request.channel_id and request.thread_ts and not thread_id:
        try:
            thread_id = await common.lookup_slack_thread_id(request.channel_id, request.thread_ts)
        except Exception:  # noqa: BLE001
            thread_id = None
    if thread_id:
        await _mark_slack_thread_errored(thread_id, request, repo)
    await report_slack_failure(request.model_copy(update={"thread_id": thread_id}).target, exc)


@dataclass(frozen=True)
class AccountLinkNeeded:
    """A Slack user Open SWE can't start a run for until they connect their account."""

    user_id: str
    user_email: str | None
    reason: common.AccountLinkReason

    async def prompt(self, channel_id: str, thread_ts: str) -> None:
        """Prompt them ephemerally to connect their account."""
        await common.post_account_link_prompt(
            channel_id, thread_ts, self.user_id, self.user_email, reason=self.reason, langsmith=True
        )


@dataclass(frozen=True)
class SlackAuthorization:
    """A Slack identity's validated sign-in and optional GitHub capability."""

    slack_user_id: str
    user: User | None
    identity: ThreadIdentity | None
    reason: common.AccountLinkReason | None = None
    github_available: bool = True

    @classmethod
    async def resolve(cls, slack_user_id: str) -> SlackAuthorization:
        user = await User.for_identity("slack", slack_user_id) if slack_user_id else None
        if user is None:
            return cls(slack_user_id, None, None, "unlinked")
        try:
            await LangSmithLogin().authorize_user(user)
        except HTTPException as exc:
            if exc.status_code not in {401, 403}:
                raise
            common.logger.info(
                "Slack sender requires LangSmith sign-in",
                extra={"person_id": str(user.id), "status_code": exc.status_code},
            )
            return cls(slack_user_id, user, None, "revoked")
        login = user.github_login
        github_available = await common.account_link_reason(login) is None
        return cls(
            slack_user_id,
            user,
            ThreadIdentity(login or f"user:{user.id}"),
            github_available=github_available,
        )

    @property
    def needed(self) -> AccountLinkNeeded | None:
        return AccountLinkNeeded(self.slack_user_id, None, self.reason) if self.reason else None

    @property
    def github_login(self) -> str | None:
        return self.identity.github_login if self.identity and self.github_available else None

    def subject_for(self, metadata: Mapping[str, object]) -> str:
        if self.identity is None or self.user is None:
            raise HTTPException(401, "LangSmith sign-in required")
        owner = metadata.get("owner_login")
        subjects = {f"user:{self.user.id}", self.user.github_login}
        if isinstance(owner, str) and owner in subjects:
            return owner
        return self.identity.subject

    def configure(self, configurable: dict[str, JsonValue]) -> None:
        if self.user is None or self.identity is None:
            raise HTTPException(401, "LangSmith sign-in required")
        configurable["user_id"] = str(self.user.id)
        if self.github_login:
            configurable["github_login"] = self.github_login
        else:
            configurable.pop("github_login", None)
            configurable.update(
                github_linked=False,
                auth_provider="langsmith",
                repo=None,
                repo_explicitly_none=True,
            )


async def account_link_needed(user_id: str) -> AccountLinkNeeded | None:
    """Why a human Slack sender cannot run without explicitly linked LangSmith sign-in."""
    return (await SlackAuthorization.resolve(user_id)).needed


async def workspace_scoped_default_repo(
    candidate: Repo | None, workspace: str | None, login: str | None = None
) -> Repo | None:
    """Keep a defaulted repository unless another workspace prefers it.

    Nobody named this repository, so it did not pick the workspace, and the
    workspace the run landed in should not inherit a default meant for another:
    its own default repository takes over when it has one. Every workspace can
    use every repository, so the candidate stays otherwise.
    """
    personal = await common.get_profile_default_repo(login, workspace, fallback_global=False)
    if personal:
        return Repo.model_validate(personal)
    if not workspace or candidate is None:
        return candidate
    preferred_by = await workspace_for_repo(candidate.owner, candidate.name)
    if preferred_by is None or preferred_by == workspace:
        return candidate
    scoped = (await common.get_workspace_settings(workspace)).default_repo
    return Repo.model_validate(scoped) if scoped else candidate


async def _resolved[T](value: T) -> T:
    """``value`` as an awaitable, for a branch of an ``asyncio.gather`` with nothing to do."""
    return value


@dataclass(frozen=True)
class SlackSenderProfile:
    """What Slack's profile says about the person who sent a message."""

    email: str | None = None
    name: str = ""
    timezone: str = ""

    @classmethod
    async def lookup(cls, user_id: str) -> SlackSenderProfile:
        """The profile Slack reports, empty when Slack does not answer."""
        if not user_id:
            return cls()
        slack_user = await common.get_slack_user_info(user_id)
        if not slack_user:
            return cls()
        email: str | None = None
        name = ""
        profile = slack_user.get("profile", {})
        if isinstance(profile, dict):
            email = profile.get("email")
            name = (
                profile.get("display_name")
                or profile.get("real_name")
                or slack_user.get("real_name")
                or slack_user.get("name")
                or ""
            )
        timezone = slack_user.get("tz")
        return cls(
            email=email,
            name=name,
            timezone=timezone.strip() if isinstance(timezone, str) else "",
        )


async def slack_login(user_id: str, *, thread_id: str | None = None) -> str | None:
    """The authorized thread subject of an explicitly linked Slack sender."""
    authorization = await SlackAuthorization.resolve(user_id)
    if authorization.identity is None:
        return None
    metadata = (await common.ThreadRecord.read(thread_id)).metadata if thread_id else {}
    return authorization.subject_for(metadata)


def _slack_thread_title(request_text: str, concierge_mode: bool, name: str) -> str:
    """A DM thread is named for the person, not for whatever they asked first."""
    return dm_thread_title(name) if concierge_mode else request_text


def _slack_thread_visibility(channel_context: SlackChannelContext | None) -> str:
    """Bot DMs are private to the person; anything in a channel is collaborative."""
    if channel_context is not None and channel_context.is_im is True:
        return "private"
    return "public"


async def _mark_slack_thread_errored(
    thread_id: str, request: SlackRequest, repo: Repo | None
) -> None:
    try:
        owner_login = await slack_login(request.user_id)
        visibility = _slack_thread_visibility(request.channel_context)
        concierge_mode = request.concierge_mode or is_concierge_thread(
            request.channel_context, request.thread_ts
        )
        # An unlinked sender is turned away at the account gate; a private thread
        # nobody owns would be unreachable, so persist nothing for them.
        if not request.triggering_bot_id and (visibility == "public" or owner_login):
            clean_text = (
                common.strip_bot_mention(
                    request.text, request.bot_user_id, bot_username=common.SLACK_BOT_USERNAME
                )
                or "Slack request"
            )
            await common.upsert_agent_thread_metadata(
                thread_id,
                source="slack",
                repo_config=repo.model_dump() if repo else None,
                title=_slack_thread_title(clean_text, concierge_mode, request.user_name),
                static_title=concierge_mode,
                source_context=SourceContext(
                    slack_thread=SlackThreadRef(
                        channel_id=request.channel_id,
                        thread_ts=request.thread_ts,
                        triggering_user_id=request.user_id,
                        triggering_event_ts=request.event_ts,
                    )
                ),
                visibility=visibility,
                owner_login=owner_login or "",
                identity=ThreadIdentity(owner_login) if owner_login else None,
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
    except Exception as exc:  # noqa: BLE001
        if request.triggering_bot_id and common.is_not_found_error(exc):
            return
        common.logger.warning("Could not mark Slack thread %s as errored", thread_id, exc_info=True)


async def process_slack_web_mention(
    request: SlackRequest,
    repo: common.SlackRepoResolution | None,
    *,
    inherited_workspace: str | None = None,
    model_choice: tuple[str, str | None] | None = None,
) -> bool:
    """Dispatch a web-only request seeded from Slack without Slack delivery."""
    return await _process_slack_mention_impl(
        request.model_copy(update={"web_only": True}),
        repo,
        inherited_workspace=inherited_workspace,
        model_choice=model_choice,
    )


@dataclass
class SlackChannelRun:
    """The run a Slack message starts, prepared for MDA's Slack channel to start it."""

    input: RunInput
    configurable: dict[str, JsonValue]
    trigger_ts: str
    concierge_message_ts: str = ""
    # Whether the message redirects the thread's work in progress; otherwise it
    # waits its turn, as the backend's own Slack follow-ups do.
    interrupts: bool = True


@dataclass(frozen=True)
class SlackTurnRead:
    """What MDA's Slack channel read from Slack for a turn whose prompt it builds."""

    # The request's text as the prompt has it, which titles a new thread.
    clean_text: str
    # The people whose messages the prompt may quote, and who spoke in the Slack thread.
    context_user_ids: list[str]
    participant_user_ids: list[str]
    has_images: bool = False
    # The Slack thread holds the agent's review request card.
    review_card: bool = False


@dataclass
class _ChannelRunCapture:
    run: SlackChannelRun | None = None
    # A turn whose prompt MDA's channel builds from what it read: the run's input
    # stays empty, and ``prompt`` holds what the prompt needs from Open SWE's records.
    read: SlackTurnRead | None = None
    prompt: SlackTurnPrompt | None = None


async def prepare_slack_channel_run(
    request: SlackRequest,
    repo: common.SlackRepoResolution | None,
    *,
    read: SlackTurnRead | None = None,
) -> tuple[SlackChannelRun | None, SlackTurnPrompt | None]:
    """The run a message in an MDA-owned Slack conversation starts, without starting it.

    Everything else happens as for the backend's own Slack runs (the gates, the
    account-link prompt, the thread's metadata, files staged in the sandbox);
    MDA's Slack channel starts the run and shows its status (``openswe.slack.channel``).
    Account-link prompts are ephemeral replies. With ``read``, the
    channel read Slack and builds the prompt itself: Slack isn't read here, and the
    prompt's facts come back with the run.
    """
    capture = _ChannelRunCapture(read=read)
    await _process_slack_mention_impl(
        request, repo, inherited_workspace=request.inherited_workspace, channel_run=capture
    )
    return capture.run, capture.prompt


async def _process_slack_mention_impl(
    request: SlackRequest,
    repo_resolution: common.SlackRepoResolution | None,
    *,
    inherited_workspace: str | None = None,
    model_choice: tuple[str, str | None] | None = None,
    status_shown: asyncio.Future[bool] | None = None,
    channel_run: _ChannelRunCapture | None = None,
) -> bool:
    resolution = repo_resolution or common.SlackRepoResolution()
    repo = resolution.repo
    channel_id = request.channel_id
    thread_ts = request.thread_ts
    event_ts = request.event_ts
    user_id = request.user_id
    text = request.text
    attachments = request.attachments
    bot_user_id = request.bot_user_id
    message_update = request.message_update
    reply_thread_ts = request.reply_thread_ts
    original_message_ts = request.original_message_ts or event_ts
    channel_context = (
        request.channel_context
        if request.channel_context is not None
        else SlackChannelContext(id=channel_id)
    )
    treat_all_messages_as_mentions = request.treat_all_messages_as_mentions
    concierge_mode = not request.web_only and (
        request.concierge_mode or is_concierge_thread(channel_context, thread_ts)
    )
    # MDA's Slack channel read Slack already: it builds the prompt from what it read.
    read = channel_run.read if channel_run is not None else None

    if not channel_id or not thread_ts or not event_ts:
        common.logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return False

    langgraph_client = get_langgraph_client()
    thread_id = request.thread_id or await common.resolve_slack_thread_id(channel_id, thread_ts)
    allowed_bot = None
    if request.triggering_bot_id:
        allowed_bot = await resolve_allowed_slack_bot(
            request.team_id,
            request.triggering_bot_id,
            user_id=user_id,
            app_id=request.triggering_bot_app_id,
        )
        if allowed_bot is None:
            return False
        if _slack_thread_visibility(channel_context) == "private":
            return False
        try:
            existing_thread = await langgraph_client.threads.get(thread_id)
        except Exception as exc:
            if not common.is_not_found_error(exc):
                raise
        else:
            existing_metadata = existing_thread.get("metadata") or {}
            opening_slack = SourceContext.from_metadata(existing_metadata).slack_thread
            if not common.ThreadRecord.is_unowned_placeholder(existing_metadata) and (
                existing_metadata.get("owner_type") != "system"
                or existing_metadata.get("visibility") != "public"
                or opening_slack is None
                or opening_slack.triggering_bot_id != allowed_bot.bot_id
                or opening_slack.team_id != allowed_bot.team_id
            ):
                common.logger.info(
                    "Ignoring Slack bot mention in a thread with another owner",
                    extra={"agent_thread_id": thread_id, "slack_bot_id": allowed_bot.bot_id},
                )
                return False
    sender_profile = asyncio.ensure_future(
        SlackSenderProfile.lookup(user_id)
        if allowed_bot is None
        else _resolved(SlackSenderProfile(name=allowed_bot.name))
    )
    context_thread_ts = request.context_thread_ts or reply_thread_ts or thread_ts
    authorization, thread_record, thread_messages, sender = await asyncio.gather(
        SlackAuthorization.resolve(user_id) if allowed_bot is None else _resolved(None),
        common.ThreadRecord.read(thread_id),
        _resolved([])
        if message_update or read is not None
        else common.fetch_slack_thread_messages(
            request.context_channel_id or channel_id, context_thread_ts
        ),
        sender_profile,
    )
    user_email, user_name, user_timezone = sender.email, sender.name, sender.timezone
    if authorization is not None and (needed := authorization.needed) is not None:
        await needed.prompt(channel_id, reply_thread_ts or thread_ts)
        return False
    identity = authorization.identity if authorization else None
    mapped_login = authorization.github_login if authorization else None
    if authorization is not None and mapped_login is None:
        repo = None
        resolution = common.SlackRepoResolution()
    thread_metadata = thread_record.authorize(
        authorization.subject_for(thread_record.metadata) if authorization else "",
        bot=allowed_bot is not None,
    )
    # A new thread's metadata links back to Slack. Looking that link up here keeps
    # it off the metadata write, which would otherwise wait on it before dispatch.
    opening_permalink = asyncio.ensure_future(
        common.get_slack_permalink(channel_id, thread_ts)
        if not thread_record.started and not request.web_only
        else _resolved(None)
    )
    # The thread's state only matters once dispatching, but reading it is one of
    # the slowest lookups here and nothing below changes it before the run.
    holder = held_context_thread(request, thread_id, exists=thread_record.exists)
    dispatched_context = asyncio.ensure_future(
        _dispatched_slack_context(holder)
        if holder is not None and read is None
        else _resolved((set[str](), set[str]()))
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
                **(
                    {"bot_id": allowed_bot.bot_id, "bot_profile": {"name": allowed_bot.name}}
                    if allowed_bot is not None
                    else {}
                ),
            }
        )
    elif current_message is not None and attachments and not current_message.get("attachments"):
        current_message["attachments"] = attachments

    context_messages = (
        sorted(thread_messages, key=lambda message: common.parse_slack_ts(message.get("ts")))
        if request.context_thread_ts
        else common.select_slack_context_messages(
            thread_messages,
            event_ts,
            bot_user_id,
            common.SLACK_BOT_USERNAME,
            treat_all_messages_as_mentions=treat_all_messages_as_mentions,
            held_timestamps=(
                set[str]() if holder is None else (await dispatched_context)[1] or None
            ),
        )[0]
    )
    source_messages = (
        [{"ts": event_ts, "text": text, "user": user_id, "attachments": attachments}]
        if message_update
        else context_messages
    )
    context_user_ids = (
        list(read.context_user_ids)
        if read is not None
        else [
            value
            for value in (message.get("user") for message in context_messages)
            if isinstance(value, str) and value
        ]
    )
    message_texts = [
        text,
        common.format_slack_messages_for_prompt([*context_messages, *source_messages], {}),
    ]
    mentioned_user_ids = [
        mentioned
        for message_text in message_texts
        for mentioned in slack_utils.slack_mentioned_user_ids(message_text)
    ]
    (
        user_names_by_id,
        channel_names_by_id,
        (logins_by_user_id, person_ids_by_user_id),
        _,
    ) = await asyncio.gather(
        # The sender's name came with their profile; Slack is asked only for the rest.
        _resolved({})
        if read is not None
        else common.get_slack_user_names(
            [
                named
                for named in (*context_user_ids, *mentioned_user_ids)
                if named != user_id or allowed_bot is not None or not user_name
            ]
        ),
        _resolved({})
        if read is not None
        else _public_slack_channel_names(
            [
                mentioned
                for message_text in message_texts
                for mentioned in slack_utils.slack_mentioned_channel_ids(message_text)
            ]
        ),
        _slack_accounts_by_user_id([*context_user_ids, user_id]),
        persist_display_name(user_id, user_name) if allowed_bot is None else _resolved(None),
    )
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    # A private thread's prompt quotes only the people who may prompt it, and Robin's notices.
    # A conversation MDA's channel just claimed is private only as a placeholder until this
    # turn writes its visibility, so it quotes as that visibility says.
    private = (
        common.thread_is_private(thread_metadata)
        if common.visibility_is_settled(thread_metadata)
        else request.web_only or _slack_thread_visibility(channel_context) == "private"
    )
    promptable_user_ids: list[str] | None = None
    if private and not common.visibility_is_settled(thread_metadata) and identity is not None:
        thread_metadata = {
            **thread_metadata,
            "source": "slack",
            "visibility": "private",
            "owner_login": identity.subject,
        }
    if private:
        context_messages = SlackNotice.private_thread_messages(
            context_messages,
            lambda message: (
                common.thread_is_promptable(
                    thread_metadata, logins_by_user_id.get(str(message.get("user") or ""), "")
                )
                or common.thread_is_promptable(
                    thread_metadata, person_ids_by_user_id.get(str(message.get("user") or ""), "")
                )
            ),
            bot_user_id=bot_user_id,
        )
        if not message_update:
            source_messages = context_messages
        promptable_user_ids = [
            context_user
            for context_user in dict.fromkeys(context_user_ids)
            if common.thread_is_promptable(thread_metadata, logins_by_user_id.get(context_user, ""))
            or common.thread_is_promptable(
                thread_metadata, person_ids_by_user_id.get(context_user, "")
            )
        ]
    parsed = ParsedSlackMessage.parse(text, bot_user_id, common.SLACK_BOT_USERNAME)
    performance_command = (
        parsed.performance_model
        and not message_update
        and allowed_bot is None
        and not concierge_mode
    )
    is_first_mention = not thread_record.started
    # A `/workspace:<name>` command on the message that opens a thread is one
    # input to which workspace its sandbox boots from — resolved below, once the
    # triggering user's GitHub login is known. Only the opening message can pick
    # it: the sandbox is created once, so honoring a later one would change the
    # prompt but not the image. It is stripped only when it names a real
    # workspace, so a typo stays visible in the transcript instead of vanishing.
    tagged_slug: str | None = None
    if is_first_mention and parsed.workspace:
        if await WORKSPACES.get(parsed.workspace) is not None:
            tagged_slug = parsed.workspace
        else:
            common.logger.info(
                "Slack thread tagged an unknown workspace",
                extra={"slack_thread_id": thread_id, "tagged_workspace": parsed.workspace},
            )
    clean_text = (
        slack_utils.replace_bot_mention_with_username(
            parsed.without(
                performance_model=performance_command, workspace=tagged_slug is not None
            ),
            bot_user_id,
            common.SLACK_BOT_USERNAME,
        ).strip()
        or "(no text in mention)"
    )
    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = (
        ("", [])
        if read is not None
        else await common.resolve_slack_links_in_context(source_messages, user_names_by_id)
    )

    clean_text = _label_slack_mentions(clean_text, user_names_by_id, channel_names_by_id)
    content_blocks: list[dict[str, Any]] = [cast(dict[str, Any], create_text_block(clean_text))]

    image_urls = (
        []
        if read is not None
        else common.dedupe_urls(
            [
                url
                for msg in source_messages
                for url in common.extract_image_urls(msg.get("text", ""))
            ]
            + [url for msg in source_messages for url in slack_utils.slack_image_file_urls(msg)]
            + image_urls_from_links
        )
    )

    # A DM always answers on the person's own default model: a per-thread model
    # choice is never routed into it.
    thread_model_choice = None if concierge_mode else model_choice or thread_record.model_choice

    # Routing comes before the run's repository: a repository nobody named
    # must not outrank the channel's binding, and once a workspace has won, a
    # default repository another workspace owns has to give way to its own.
    # Later mentions carry no tag, so the thread's workspace comes back from
    # metadata — a follow-up must not be told about `default` while its sandbox
    # was built from the workspace the opening message resolved to. It is
    # resolved here, before the model is, because the model default and the
    # Fable flag are the resolved workspace's.
    if is_first_mention:
        # A DM is one person's own space rather than a routed channel, so it opens
        # in the instance default unless they named a workspace; an environment
        # tool can move it afterwards and metadata carries that to later messages.
        thread_workspace = (
            DEFAULT_WORKSPACE_SLUG
            if concierge_mode and not tagged_slug
            else (
                await resolve_workspace(
                    thread_workspace=inherited_workspace,
                    tag=tagged_slug,
                    repo=resolution.routing_repo,
                    slack_channel_id=channel_id,
                    login=mapped_login,
                )
            ).slug
        )
    else:
        thread_workspace = thread_record.workspace

    image_model_override: tuple[str, str] | None = None
    if performance_command:
        stored_settings = await load_thread_settings(langgraph_client, thread_id)
        stored_performance = stored_settings.get("routing_models", {}).get("performance")
        if stored_performance:
            thread_model_choice = (stored_performance["model_id"], stored_performance["effort"])
        else:
            settings = await cached_workspace_settings(thread_workspace)
            thread_model_choice = settings.agent_routing_models["performance"]

    if image_urls or (read is not None and read.has_images):
        resolved_model_id = thread_model_choice[0] if thread_model_choice else None
        if resolved_model_id is None:
            thread_settings = await load_thread_settings(langgraph_client, thread_id)
            resolved_model_id = thread_settings.get("model_id")
        if resolved_model_id is None:
            resolved_model_id = await common.resolve_agent_model_id(
                mapped_login, workspace=thread_workspace
            )
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
    # MDA's Slack channel fetches the images of a prompt it builds.
    if image_urls:
        common.logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=common.DEFAULT_HTTP_TIMEOUT
        ) as http_client:
            for image_url in image_urls:
                image_block = await common.fetch_image_block(image_url, http_client)
                if image_block:
                    content_blocks.append(cast(dict[str, Any], image_block))

    slack_thread_context: dict[str, Any] = {
        "channel_id": channel_id,
        "channel_context": channel_context.dump(),
        "thread_ts": thread_ts,
        "triggering_user_id": user_id,
        "triggering_user_name": user_name,
        "triggering_user_email": user_email or "",
        "triggering_event_ts": event_ts,
    }
    if request.breakout_root_suffix is not None:
        slack_thread_context["breakout_root_suffix"] = request.breakout_root_suffix
    if user_timezone:
        slack_thread_context["triggering_user_timezone"] = user_timezone
    if allowed_bot is not None:
        slack_thread_context["triggering_bot_id"] = allowed_bot.bot_id
        slack_thread_context["triggering_bot_app_id"] = allowed_bot.app_id
        slack_thread_context["team_id"] = allowed_bot.team_id
    if concierge_mode and reply_thread_ts:
        slack_thread_context["reply_thread_ts"] = reply_thread_ts

    review_card = (
        await HumanReviewRequest.card_in_thread(channel_id, thread_ts)
        if event_ts != thread_ts
        and context_thread_ts == thread_ts
        and (
            read.review_card
            if read is not None
            else any(
                slack_utils.is_own_slack_message(message, bot_user_id)
                and isinstance(message.get("text"), str)
                and message["text"].startswith(
                    ("Review requested for ", "Expedited review requested for ")
                )
                for message in thread_messages
            )
        )
        else None
    )
    if review_card is not None and not resolution.explicit and (allowed_bot or mapped_login):
        reviewed = review_card.pull_request
        repo = Repo.model_validate({"owner": reviewed.owner, "name": reviewed.repo})
    elif not resolution.explicit and (allowed_bot or mapped_login):
        repo = await workspace_scoped_default_repo(repo, thread_workspace, mapped_login)
    repo_dict = repo.model_dump() if repo else None

    channel_identity = await _slack_channel_identity(
        channel_id, thread_ts, channel_context, thread_id=thread_id, repo=repo
    )

    # Guidance that holds for the whole thread, deduped by content so the model
    # is told once; only what this turn adds travels as a message.
    try:
        ask_channel = await WORKSPACES.is_ask_channel(channel_id)
    except Exception:
        common.logger.warning(
            "Ask channel lookup failed; retaining normal Slack guidance",
            extra={"channel_id": channel_id},
            exc_info=True,
        )
        ask_channel = False
    constant_context = "\n\n".join(
        section
        for section in (
            _CONCIERGE_CONTEXT if concierge_mode else "",
            prompt("runs/slack-ask-channel")
            if ask_channel
            else (_KITCHEN_CONTEXT if request.kitchen_channel else _NON_KITCHEN_CONTEXT),
        )
        if section
    )
    # A thread started under a DM Open SWE sent for another thread is about that thread's work.
    dm_origin = (
        await DmOrigin.of(channel_id, thread_ts)
        if is_first_mention
        and not concierge_mode
        and event_ts != thread_ts
        and is_dm_channel(channel_context)
        else None
    )
    dm_origin_section = ""
    if dm_origin is not None:
        origin_permalink, origin_thread_id = await asyncio.gather(
            slack_utils.get_slack_permalink(*dm_origin.location),
            common.lookup_slack_thread_id(*dm_origin.location),
        )
        dm_origin_section = prompt(
            "slack/dm-origin",
            origin=dm_origin,
            permalink=origin_permalink or "",
            agent_thread_id=origin_thread_id or "",
        )
    review_section = (
        prompt("runs/slack-review-request", pull_request=review_card.pull_request)
        if review_card is not None
        else ""
    )
    turn_context = "\n\n".join(
        section
        for section in (
            _MESSAGE_UPDATE_PREAMBLE if message_update else "",
            dm_origin_section,
            request.turn_context,
            review_section,
            resolved_links_section,
        )
        if section
    )

    configurable: dict[str, Any] = {
        "repo": repo_dict,
        "slack_thread": slack_thread_context,
        "user_email": user_email,
        "source": "slack",
        "slack_kickoff_eligible": False,
        "slack_action_token": request.action_token,
        "slack_search_message_ts": request.original_message_ts,
    }

    if authorization is not None:
        authorization.configure(configurable)
    if mapped_login:
        configurable["github_login"] = mapped_login
        logins_by_user_id[user_id] = mapped_login
    # A DM is reachable by exactly one person, so the admin capability cannot leak
    # to anyone else; the factory still rechecks the sender against the configured
    # admins, and a non-admin's DM gets nothing extra.
    if is_dm_channel(channel_context):
        configurable["admin_thread"] = True
    if request.context_thread_ts:
        configurable["slack_breakout"] = True
    if thread_workspace:
        configurable["workspace"] = thread_workspace
        configurable["environment"] = thread_workspace
    if image_model_override:
        configurable["agent_model_id"] = image_model_override[0]
        configurable["agent_effort"] = image_model_override[1]
        configurable["model_override_reason"] = "image_input"

    if thread_model_choice and not image_model_override:
        configurable["agent_model_id"], configurable["agent_effort"] = thread_model_choice
        configurable["model_selection"] = "explicit"

    configurable["slack_kickoff_eligible"] = (
        is_first_mention and not concierge_mode and not request.context_thread_ts
    )
    langgraph_client = get_langgraph_client()
    visibility = "private" if request.web_only else _slack_thread_visibility(channel_context)
    if request.web_only:
        configurable.pop("slack_thread", None)
        configurable.pop("slack_breakout", None)
        configurable.pop("slack_action_token", None)
        configurable.pop("slack_search_message_ts", None)
        configurable.pop("admin_thread", None)
        configurable["source"] = "web"
    source_context = (
        None
        if request.web_only
        else SourceContext.parse({"slack_thread": configurable["slack_thread"]})
    )
    opened_at = source_context.slack_thread if source_context is not None else None
    if opened_at is not None and (permalink := await opening_permalink):
        opened_at.permalink = permalink
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="web" if request.web_only else "slack",
        repo_config=repo_dict,
        github_login=mapped_login or "",
        user_email=user_email or "",
        # A DM offers its title on every message: the upsert keeps the first one
        # written, so a name Slack could not resolve earlier still lands later.
        title=_slack_thread_title(
            read.clean_text if read is not None else clean_text, concierge_mode, user_name
        )
        if (is_first_mention or concierge_mode)
        else "",
        static_title=concierge_mode,
        source_context=source_context,
        workspace=thread_workspace,
        # Everyone who has spoken in the Slack thread keeps their Open SWE
        # participant credit, so a later message from any one of them refreshes
        # the whole set rather than only the latest sender.
        slack_participant_user_ids=(
            [
                participant
                for participant in read.participant_user_ids
                if promptable_user_ids is None or participant in promptable_user_ids
            ]
            if read is not None
            else slack_participant_ids(context_messages)
        )
        if not is_first_mention
        else [],
        visibility=visibility,
        owner_login=identity.subject if identity else "",
        identity=identity,
        owner_type="system" if allowed_bot else "user",
    )
    if (visibility == "private" or allowed_bot is not None) and not persisted:
        # Dispatch would create the thread itself, with no metadata and so public.
        raise RuntimeError("could not persist thread authorization metadata")

    # An edit corrects a request the agent already has, so it belongs in the
    # thread's message queue rather than in a run of its own. Nothing drains that
    # queue while the thread is idle; an edit made after the agent finished waits
    # for the next message.
    if message_update and await queue_message_for_thread(
        thread_id, [{"type": "text", "text": _MESSAGE_UPDATE_PREAMBLE}, *content_blocks]
    ):
        common.logger.info("Queued Slack message edit for thread %s", thread_id)
        return False

    # A channel run doesn't stage files here: MDA's channel starts its sandbox.
    if persisted and channel_run is None:
        staged_files = await queue_slack_files(thread_id, slack_file_entries(source_messages))
        if staged_files:
            files_section = slack_files_section(staged_files)
            turn_context = f"{turn_context}\n\n{files_section}" if turn_context else files_section

    # Anything said in a DM is said to Open SWE, and the person expects the next
    # thing they type to redirect the work in front of them rather than queue
    # behind it.
    interrupt_active_run = (
        concierge_mode
        or request.kitchen_channel
        or _interrupts_active_run(
            text,
            bot_user_id,
            treat_all_messages_as_mentions=treat_all_messages_as_mentions,
            message_update=message_update,
            explicit_request=request.explicit_request or performance_command,
        )
    )
    if read is not None and channel_run is not None:
        # MDA's Slack channel builds the prompt with these.
        channel_run.prompt = SlackTurnPrompt(
            first_turn=is_first_mention,
            sender_name=user_name,
            logins=logins_by_user_id,
            person_ids=person_ids_by_user_id,
            promptable_user_ids=promptable_user_ids,
            channel=cast(dict[str, JsonValue], channel_identity),
            turn_sections=[
                section
                for section in (dm_origin_section, request.turn_context, review_section)
                if section
            ],
            ask_channel=ask_channel,
        )
        channel_run.run = SlackChannelRun(
            input={"messages": []},
            configurable=configurable,
            trigger_ts=event_ts,
            interrupts=interrupt_active_run,
        )
        return True
    visible_context_hashes, dispatched_timestamps = await dispatched_context
    # The run describes the sender and, on a follow-up, every linked person in
    # the Slack thread — the same set persisted as this thread's participants.
    described_slack_ids = {user_id} if is_first_mention else set(person_ids_by_user_id)
    run_input = _slack_context_input(
        context_messages,
        user_names_by_id,
        logins_by_user_id,
        person_ids_by_user_id=person_ids_by_user_id,
        channel_names_by_id=channel_names_by_id,
        channel=channel_identity,
        bot_user_id=bot_user_id,
        event_ts=event_ts,
        source_message_ts=request.source_message_ts,
        context_channel_id=request.context_channel_id,
        trigger_user_id=user_id,
        request_text=clean_text,
        request_blocks=content_blocks,
        prior_message_text=request.prior_message_text,
        turn_context=turn_context,
        constant_context="" if request.web_only else constant_context,
        web_only=request.web_only,
        dispatched_timestamps=dispatched_timestamps,
        run_described_person_ids={
            person_id
            for slack_id in described_slack_ids
            if (person_id := person_ids_by_user_id.get(slack_id))
        },
        visible_context_hashes=visible_context_hashes,
        trigger_system=_slack_bot_identity(allowed_bot)
        if allowed_bot is not None
        else request.trigger_system,
        explicit_mention=request.explicit_mention or _mentions_open_swe(text, bot_user_id),
    )
    receipt_ts = original_message_ts if concierge_mode and not message_update else ""
    if receipt_ts:
        await slack_utils.add_slack_reaction(channel_id, receipt_ts, "hourglass_flowing_sand")
    if channel_run is not None:
        channel_run.run = SlackChannelRun(
            input=run_input,
            configurable=configurable,
            trigger_ts=event_ts,
            concierge_message_ts=receipt_ts,
            interrupts=interrupt_active_run,
        )
        return True
    if status_shown is not None:
        await asyncio.wait({status_shown})
    try:
        run = await _dispatch_or_queue_slack_run(
            langgraph_client,
            thread_id,
            run_input,
            configurable,
            interrupt_active_run=interrupt_active_run,
            trigger_ts=event_ts,
            concierge_message_ts=receipt_ts,
        )
    except Exception:
        common.logger.exception("Slack run dispatch failed", extra={"agent_thread_id": thread_id})
        raise
    common.logger.info(
        "Slack LangGraph run %s dispatched for thread %s",
        common.run_id_for_logging(run),
        thread_id,
    )
    # A routed thread ignores the pick (the run still records it), so nothing switched.
    # An image the performance model cannot read runs on a vision model instead.
    if (
        performance_command
        and thread_model_choice
        and not image_model_override
        and not await RoutingArm.routes(thread_id, thread_workspace)
    ):
        await slack_utils.post_slack_ephemeral_message(
            channel_id,
            user_id,
            "Switched to the performance model.",
            thread_ts=reply_thread_ts or thread_ts,
        )
    run_id = run.get("run_id")
    if performance_command and thread_model_choice and isinstance(run_id, str) and run_id:
        selected_model = image_model_override or thread_model_choice
        await create_langsmith_feedback(
            run_id,
            "performance_model_switch_slack",
            score=1,
            source_info={"model_id": selected_model[0], "effort": selected_model[1]},
        )
    if request.web_only:
        return bool(isinstance(run_id, str) and run_id)
    if is_first_mention:
        if isinstance(run_id, str) and run_id:
            await common.store_slack_run_mapping(
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
                channel_id,
                thread_ts,
                run_id,
                message_ts=original_message_ts,
                triggering_user_id=user_id,
                agent_thread_id=thread_id,
            )
    if not concierge_mode and isinstance(run_id, str) and run_id:
        await show_slack_thinking_status(
            client=langgraph_client,
            thread_id=thread_id,
            run_id=run_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
    return bool(isinstance(run_id, str) and run_id)
