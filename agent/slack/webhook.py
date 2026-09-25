"""Slack webhook handler — moved out of common.py (behavior-identical).

Helpers and constants stay in common.py; they are accessed through the module
object (``common.X``) so tests that monkeypatch them keep working.
"""

import posixpath
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

import httpx2
from langchain_core.messages.content import create_text_block

from agent.input_messages import (
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
from agent.prompts import load_prompt
from agent.run_config import Repo
from agent.slack import client as slack_utils
from agent.slack.allowed_bots import AllowedSlackBot, resolve_allowed_slack_bot
from agent.slack.dm import dm_thread_title, is_concierge_thread, is_dm_channel
from agent.slack.failures import report_slack_failure
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.slack.thinking import (
    clear_slack_thinking_status_if_idle,
    restore_slack_thinking_status,
    show_slack_thinking_status,
    stream_slack_thinking_steps,
)
from agent.source_context import SlackThreadRef, SourceContext
from agent.users import User, persist_display_name
from agent.utils.json_types import as_json_object
from agent.utils.langsmith import get_langsmith_trace_url
from agent.utils.thread_ops import (
    langgraph_client as get_langgraph_client,
)
from agent.utils.thread_ops import queue_message_for_thread
from agent.webhooks import common
from agent.workspaces.routing import resolve_workspace, workspace_for_repo
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES, parse_workspace_tag

_CODE_CHANNEL_CONTEXT = load_prompt("runs/slack-code-channel.md")
_CONCIERGE_CONTEXT = load_prompt("runs/slack-concierge.md")
_MESSAGE_UPDATE_PREAMBLE = load_prompt("runs/slack-message-update.md")


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


async def _dispatch_or_queue_slack_run(
    client: Any,
    thread_id: str,
    run_input: RunInput | list[dict[str, Any]],
    configurable: dict[str, Any],
    *,
    explicitly_tagged: bool,
    trigger_ts: str,
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
            thread_title=None,
            input=run_input,
            metadata={**common.AGENT_VERSION_METADATA, "slack_trigger_ts": trigger_ts},
            client=client,
            multitask_strategy="interrupt" if explicitly_tagged else "enqueue",
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


async def _dispatched_slack_context(client: Any, thread_id: str) -> tuple[set[str], set[str]]:
    """What the thread already holds: visible context hashes and replayed Slack ts.

    Both keep dispatch from re-sending what the model has. A thread whose state
    cannot be read is treated as empty, which repeats context rather than losing
    the turn.
    """
    try:
        state = as_json_object(await client.threads.get_state(thread_id))
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

_SLACK_FILE_DIR = "/workspace/.open-swe/slack-files"
_MAX_SLACK_FILE_ATTACHMENTS = 10


@dataclass(frozen=True)
class SlackFileEntry:
    """A non-image file Slack attached to a message."""

    name: str
    url: str


@dataclass(frozen=True)
class StagedSlackFile:
    """A Slack file staged in the thread's sandbox."""

    filename: str
    sandbox_path: str


def _slack_file_entries(messages: list[dict[str, Any]]) -> list[SlackFileEntry]:
    """Non-image files attached to the given messages, in first-seen order."""
    entries: list[SlackFileEntry] = []
    seen: set[str] = set()
    for message in messages:
        files = message.get("files")
        if not isinstance(files, list):
            continue
        for file_info in files:
            if not isinstance(file_info, dict) or file_info.get("url_private") in (None, ""):
                continue
            mimetype = file_info.get("mimetype")
            if isinstance(mimetype, str) and mimetype.startswith("image/"):
                continue
            url = str(file_info["url_private"])
            if url in seen:
                continue
            seen.add(url)
            entries.append(
                SlackFileEntry(
                    name=file_info.get("name") if isinstance(file_info.get("name"), str) else "",
                    url=url,
                )
            )
            if len(entries) >= _MAX_SLACK_FILE_ATTACHMENTS:
                return entries
    return entries


def _sanitize_slack_filename(name: str, url: str) -> str:
    filename = name.strip()
    if not filename or "/" in filename or "\x00" in filename:
        parsed = urlparse(url).path
        filename = posixpath.basename(parsed) if parsed else ""
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._") or "slack-file"
    return filename[:120]


async def _download_slack_files_to_sandbox(
    entries: list[SlackFileEntry], thread_id: str, *, workspace_slug: str | None = None
) -> list[StagedSlackFile]:
    """Download Slack files and stage them in the thread's sandbox.

    Best-effort: a missing sandbox or a failed download only skips that file.
    """
    if not entries:
        return []
    try:
        from agent.sandboxes.lifecycle import ensure_sandbox_for_thread

        backend = await ensure_sandbox_for_thread(thread_id, workspace_slug=workspace_slug)
    except Exception:
        common.logger.warning(
            "Could not reach sandbox for thread %s; skipping Slack file attachments",
            thread_id,
            exc_info=True,
        )
        return []
    staged: list[StagedSlackFile] = []
    used_names: set[str] = set()
    for entry in entries:
        try:
            content = await slack_utils.download_slack_file(entry.url)
        except slack_utils.SlackFileDownloadError as error:
            common.logger.info("Slack file download skipped", extra={"slack_error": error.code})
            continue
        filename = _sanitize_slack_filename(entry.name, entry.url)
        base = filename
        suffix = 1
        while filename in used_names:
            filename = f"{base.rsplit('.', 1)[0] if '.' in base else base}-{suffix}"
            suffix += 1
        used_names.add(filename)
        sandbox_path = posixpath.join(_SLACK_FILE_DIR, filename)
        try:
            responses = await backend.aupload_files([(sandbox_path, content)])
        finally:
            del content
        if not responses:
            continue
        response = responses[0]
        error = (
            response.get("error")
            if isinstance(response, dict)
            else getattr(response, "error", None)
        )
        if error:
            common.logger.info(
                "Slack file staging failed", extra={"slack_error": str(error), "file": filename}
            )
            continue
        staged.append(StagedSlackFile(filename, sandbox_path))
    return staged


def _slack_files_section(staged: list[StagedSlackFile]) -> str:
    lines = [
        "## Slack File Attachments",
        "These files from Slack were staged into this thread's sandbox:",
        *[
            f"- `{file.sandbox_path}` (originally uploaded to Slack as `{file.filename}`)"
            for file in staged
        ],
    ]
    return "\n".join(lines)


async def _slack_logins_by_user_id(user_ids: list[str]) -> dict[str, str]:
    """Map Slack user ids to the GitHub logins of linked Open SWE accounts."""
    logins: dict[str, str] = {}
    for user_id in {value for value in user_ids if value}:
        login = await User.login_for_slack(user_id)
        if login:
            logins[user_id] = login
    return logins


async def _slack_person_ids_by_user_id(user_ids: list[str]) -> dict[str, str]:
    """Map Slack user ids to the person entity ids of linked Open SWE accounts."""
    person_ids: dict[str, str] = {}
    for user_id in {value for value in user_ids if value}:
        user = await User.for_identity("slack", user_id)
        if user is not None:
            person_ids[user_id] = f"user:{user.id}"
    return person_ids


def _slack_person(
    user_id: str, name: str = "", github_login: str = "", person_id: str = ""
) -> PersonIdentity:
    person: PersonIdentity = {
        "id": person_id or f"slack:{user_id}",
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
    person_ids_by_user_id: dict[str, str] | None = None,
) -> tuple[str, PersonIdentity | SystemIdentity, MessageKind]:
    """Resolve a thread message to its sender id, identity, and message kind.

    Open SWE's own replies never reach here — the model wrote them and already has
    them — so a message carrying a ``bot_id`` is somebody else's app.
    """
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
        user_id,
        user_names_by_id.get(user_id, ""),
        logins_by_user_id.get(user_id, ""),
        (person_ids_by_user_id or {}).get(user_id, ""),
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
    person_ids_by_user_id: dict[str, str] | None = None,
    channel: ChannelIdentity,
    bot_user_id: str,
    event_ts: str,
    trigger_user_id: str = "",
    request_text: str,
    request_blocks: list[dict[str, Any]],
    turn_context: str = "",
    constant_context: str = "",
    dispatched_timestamps: set[str] | None = None,
    run_described_person_ids: set[str] | None = None,
    visible_context_hashes: set[str] | None = None,
    trigger_bot: AllowedSlackBot | None = None,
) -> RunInput:
    channel_entity_id = channel["id"]
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
        # The model wrote its own replies and already has them, and it has every
        # message an earlier dispatch handed it.
        if timestamp == str(event_ts) or timestamp in already_dispatched:
            continue
        if slack_utils.is_own_slack_message(message, bot_user_id):
            continue
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
            "data": {"timestamp": timestamp},
        }
        text = _slack_message_text(message, bot_user_id)
        run_messages.append(
            human_input(text, message_context)
            if kind == "human"
            else system_input(text, message_context)
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
    if trigger_bot is not None:
        trigger_sender_id, bot_identity, trigger_kind = _slack_sender(
            {"bot_id": trigger_bot.bot_id, "bot_profile": {"name": trigger_bot.name}}, {}, {}
        )
        if trigger_sender_id not in described:
            described.add(trigger_sender_id)
            add_context(system_introduction(cast(SystemIdentity, bot_identity)))
    current_message = next(
        (message for message in messages if str(message.get("ts", "")) == str(event_ts)), {}
    )
    rendered_request = _slack_message_text(current_message, bot_user_id)
    _, separator, forwarded_context = rendered_request.partition("\n")
    if separator and forwarded_context:
        request_text = f"{request_text}\n{forwarded_context}"
    request_blocks[0] = {**request_blocks[0], "text": request_text}
    run_messages.append(
        (system_input if trigger_bot is not None else human_input)(
            request_blocks,
            {
                "sender_id": trigger_sender_id,
                "channel_id": channel_entity_id,
                "surface": "slack",
                "kind": trigger_kind,
                "data": {"timestamp": event_ts},
            },
        )
    )
    return {"messages": run_messages}


async def _clear_early_status_if_idle(request: SlackRequest, status_ts: str) -> None:
    thread_id = request.thread_id
    if not thread_id:
        try:
            thread_id = await common.lookup_slack_thread_id(
                get_langgraph_client(), request.channel_id, request.thread_ts
            )
        except Exception:  # noqa: BLE001
            common.logger.warning("Could not determine whether Slack thread status is still owned")
            return
    if thread_id:
        await clear_slack_thinking_status_if_idle(
            get_langgraph_client(), thread_id, request.channel_id, status_ts
        )
    else:
        await slack_utils.set_slack_thread_status(request.channel_id, status_ts, "")


async def process_slack_mention(
    request: SlackRequest, repo: common.SlackRepoResolution | None
) -> None:
    """Process a Slack request by creating a run or queuing a mid-run message."""
    status_ts = (
        (request.reply_thread_ts or request.original_message_ts or request.event_ts)
        if request.concierge_mode
        else request.thread_ts
    )
    show_status = bool(
        request.channel_id and status_ts and not request.code_channel and not request.message_update
    )
    if show_status:
        await restore_slack_thinking_status(request.channel_id, status_ts)
    try:
        status_handed_off = await _process_slack_mention_impl(request, repo)
        if show_status and not status_handed_off:
            await _clear_early_status_if_idle(request, status_ts)
    except Exception as exc:  # noqa: BLE001
        if show_status:
            await _clear_early_status_if_idle(request, status_ts)
        await _notify_slack_processing_error(request, repo.repo if repo else None, exc)


async def _notify_slack_processing_error(
    request: SlackRequest, repo: Repo | None, exc: BaseException
) -> None:
    """Mark the agent thread errored when one exists, then always tell the Slack thread."""
    thread_id = request.thread_id
    if request.channel_id and request.thread_ts and not thread_id:
        try:
            thread_id = await common.lookup_slack_thread_id(
                get_langgraph_client(), request.channel_id, request.thread_ts
            )
        except Exception:  # noqa: BLE001
            thread_id = None
    if thread_id:
        await _mark_slack_thread_errored(thread_id, request, repo)
    await report_slack_failure(request.model_copy(update={"thread_id": thread_id}).target, exc)


async def workspace_scoped_default_repo(candidate: Repo, workspace: str | None) -> Repo | None:
    """Keep a defaulted repository unless it belongs to another workspace.

    Nobody named this repository, so it did not pick the workspace. Handing an
    `oss` run a repository `default` owns would cross the boundary the
    workspace exists to draw, so that workspace's own default repository takes
    over — and there may not be one.
    """
    if not workspace:
        return candidate
    owner = await workspace_for_repo(candidate.owner, candidate.name)
    if owner is None or owner == workspace:
        return candidate
    scoped = (await common.get_workspace_settings(workspace)).default_repo
    if not scoped:
        return None
    fallback = Repo.model_validate(scoped)
    # The workspace's default may itself be inherited from the instance record.
    fallback_owner = await workspace_for_repo(fallback.owner, fallback.name)
    if fallback_owner is None or fallback_owner == workspace:
        return fallback
    return None


async def _slack_login(user_id: str, user_email: str | None = None) -> str | None:
    """GitHub login for a Slack user: by Slack id first, then by profile email."""
    if login := await User.login_for_slack(user_id):
        return login
    if user_email is None and user_id:
        slack_user = await common.get_slack_user_info(user_id)
        profile = slack_user.get("profile") if isinstance(slack_user, dict) else None
        user_email = profile.get("email") if isinstance(profile, dict) else None
    return await User.login_for_email(user_email) if user_email else None


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
        owner_login = await _slack_login(request.user_id)
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


async def _process_slack_mention_impl(
    request: SlackRequest, repo_resolution: common.SlackRepoResolution | None
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
    code_channel = request.code_channel
    concierge_mode = request.concierge_mode or is_concierge_thread(channel_context, thread_ts)

    if not channel_id or not thread_ts or not event_ts:
        common.logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return False

    langgraph_client = get_langgraph_client()
    thread_id = request.thread_id or await common.resolve_slack_thread_id(
        langgraph_client, channel_id, thread_ts
    )
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
            if (
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
    user_email = None
    user_name = allowed_bot.name if allowed_bot is not None else ""
    user_timezone = ""
    if user_id and allowed_bot is None:
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
        await persist_display_name(user_id, user_name)

    thread_metadata = await common.authorize_github_thread(
        thread_id, (await _slack_login(user_id, user_email) or "") if allowed_bot is None else ""
    )
    context_thread_ts = request.context_thread_ts or reply_thread_ts or thread_ts
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
        )[0]
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
    person_ids_by_user_id = await _slack_person_ids_by_user_id([*context_user_ids, user_id])
    if common.thread_is_private(thread_metadata):
        context_messages = [
            message
            for message in context_messages
            if common.thread_is_promptable(
                thread_metadata, logins_by_user_id.get(str(message.get("user") or ""), "")
            )
        ]
        if not message_update:
            source_messages = context_messages
    clean_text = (
        common.strip_bot_mention(text, bot_user_id, bot_username=common.SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    is_first_mention = not await common.thread_exists(thread_id)
    # A `workspace:<name>` (or legacy `env:<name>`) tag on the message that opens
    # a thread is one input to which workspace its sandbox boots from — resolved
    # below, once the triggering user's GitHub login is known. Only the opening
    # message can pick it: the sandbox is created once, so honoring a later tag
    # would change the prompt but not the image. The tag is stripped only when it
    # names a real workspace, so a typo stays visible in the transcript instead
    # of vanishing.
    tagged_slug: str | None = None
    if is_first_mention:
        parsed_slug, text_without_tag = parse_workspace_tag(clean_text)
        if parsed_slug and await WORKSPACES.get(parsed_slug) is not None:
            tagged_slug = parsed_slug
            clean_text = text_without_tag or "(no text in mention)"
        elif parsed_slug:
            common.logger.info(
                "Slack thread tagged an unknown workspace",
                extra={"slack_thread_id": thread_id, "tagged_workspace": parsed_slug},
            )
    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = await common.resolve_slack_links_in_context(
        source_messages, user_names_by_id
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

    mapped_login = await _slack_login(user_id, user_email) if allowed_bot is None else None
    # A DM always answers on the person's own default model: a per-thread model
    # choice is never routed into it.
    thread_model_choice = (
        None if concierge_mode else await common.get_thread_model_choice(thread_id)
    )

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
                    tag=tagged_slug,
                    repo=resolution.routing_repo,
                    slack_channel_id=channel_id,
                    login=mapped_login,
                )
            ).slug
        )
    else:
        thread_workspace = await common.get_thread_workspace(thread_id)

    image_model_override: tuple[str, str] | None = None
    if image_urls:
        resolved_model_id = (
            thread_model_choice[0]
            if thread_model_choice
            else await common.resolve_agent_model_id(mapped_login, workspace=thread_workspace)
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
        common.logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
        async with httpx2.AsyncClient(timeout=common.DEFAULT_HTTP_TIMEOUT) as http_client:
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

    if allowed_bot is None and not has_valid_user_token:
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
            await common.post_account_link_prompt(
                channel_id,
                thread_ts,
                user_id,
                user_email,
                reason=reason,
                agent_thread_id=thread_id,
            )
        return False

    slack_thread_context: dict[str, Any] = {
        "channel_id": channel_id,
        "channel_context": channel_context.dump(),
        "thread_ts": thread_ts,
        "triggering_user_id": user_id,
        "triggering_user_name": user_name,
        "triggering_user_email": user_email or "",
        "triggering_event_ts": event_ts,
    }
    if user_timezone:
        slack_thread_context["triggering_user_timezone"] = user_timezone
    if allowed_bot is not None:
        slack_thread_context["triggering_bot_id"] = allowed_bot.bot_id
        slack_thread_context["triggering_bot_app_id"] = allowed_bot.app_id
        slack_thread_context["team_id"] = allowed_bot.team_id
    if (code_channel or concierge_mode) and reply_thread_ts:
        slack_thread_context["reply_thread_ts"] = reply_thread_ts

    if repo is not None and not resolution.explicit:
        repo = await workspace_scoped_default_repo(repo, thread_workspace)
    repo_dict = repo.model_dump() if repo else None

    channel_identity = await _slack_channel_identity(
        channel_id, thread_ts, channel_context, thread_id=thread_id, repo=repo
    )
    # Guidance that holds for the whole thread, deduped by content so the model
    # is told once; only what this turn adds travels as a message.
    constant_context = "\n\n".join(
        section
        for section in (
            _CODE_CHANNEL_CONTEXT if code_channel else "",
            _CONCIERGE_CONTEXT if concierge_mode else "",
        )
        if section
    )
    turn_context = "\n\n".join(
        section
        for section in (
            _MESSAGE_UPDATE_PREAMBLE if message_update else "",
            resolved_links_section,
        )
        if section
    )

    configurable: dict[str, Any] = {
        "repo": repo_dict,
        "slack_thread": slack_thread_context,
        "user_email": user_email,
        "source": "slack",
    }
    if mapped_login:
        configurable["github_login"] = mapped_login
        logins_by_user_id[user_id] = mapped_login
    # A DM is reachable by exactly one person, so the admin capability cannot leak
    # to anyone else; the factory still rechecks the sender against the configured
    # admins, and a non-admin's DM gets nothing extra.
    if is_dm_channel(channel_context):
        configurable["admin_thread"] = True
    if thread_workspace:
        configurable["workspace"] = thread_workspace
        configurable["environment"] = thread_workspace
    if image_model_override:
        configurable["agent_model_id"] = image_model_override[0]
        configurable["agent_effort"] = image_model_override[1]

    if thread_model_choice and not image_model_override:
        configurable["agent_model_id"], configurable["agent_effort"] = thread_model_choice
        configurable["model_selection"] = "explicit"

    is_first_mention = not await common.thread_exists(thread_id)
    langgraph_client = get_langgraph_client()
    # Pass the login resolved above (from the stable Slack user id) so the thread is
    # always tagged with github_login — the key the dashboard searches by. Without
    # it, upsert re-resolves from the Slack profile email, which can miss.
    visibility = _slack_thread_visibility(channel_context)
    persisted = await common.upsert_agent_thread_metadata(
        thread_id,
        source="slack",
        repo_config=repo_dict,
        github_login=mapped_login or "",
        user_email=user_email or "",
        # A DM offers its title on every message: the upsert keeps the first one
        # written, so a name Slack could not resolve earlier still lands later.
        title=_slack_thread_title(clean_text, concierge_mode, user_name)
        if (is_first_mention or concierge_mode)
        else "",
        static_title=concierge_mode,
        source_context=SourceContext.parse({"slack_thread": configurable["slack_thread"]}),
        workspace=thread_workspace,
        # Everyone who has spoken in the Slack thread keeps their Open SWE
        # participant credit, so a later message from any one of them refreshes
        # the whole set rather than only the latest sender.
        slack_participant_user_ids=[*logins_by_user_id] if not is_first_mention else [],
        visibility=visibility,
        owner_login=mapped_login or "",
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

    if persisted:
        staged_files = await _download_slack_files_to_sandbox(
            _slack_file_entries(source_messages),
            thread_id,
            workspace_slug=thread_workspace,
        )
        if staged_files:
            files_section = _slack_files_section(staged_files)
            turn_context = f"{turn_context}\n\n{files_section}" if turn_context else files_section

    # Anything said in a DM is said to Open SWE, and the person expects the next
    # thing they type to redirect the work in front of them rather than queue
    # behind it.
    explicitly_tagged = concierge_mode or _interrupts_active_run(
        text,
        bot_user_id,
        treat_all_messages_as_mentions=treat_all_messages_as_mentions,
        code_channel=code_channel,
        message_update=message_update,
        explicit_request=request.explicit_request,
    )
    visible_context_hashes, dispatched_timestamps = await _dispatched_slack_context(
        langgraph_client, thread_id
    )
    # The run describes the sender and, on a follow-up, every linked person in
    # the Slack thread — the same set persisted as this thread's participants.
    described_slack_ids = {user_id} if is_first_mention else set(logins_by_user_id)
    run_input = _slack_context_input(
        context_messages,
        user_names_by_id,
        logins_by_user_id,
        person_ids_by_user_id=person_ids_by_user_id,
        channel=channel_identity,
        bot_user_id=bot_user_id,
        event_ts=event_ts,
        trigger_user_id=user_id,
        request_text=clean_text,
        request_blocks=content_blocks,
        turn_context=turn_context,
        constant_context=constant_context,
        dispatched_timestamps=dispatched_timestamps,
        run_described_person_ids={
            person_id
            for slack_id in described_slack_ids
            if (person_id := person_ids_by_user_id.get(slack_id))
        },
        visible_context_hashes=visible_context_hashes,
        trigger_bot=allowed_bot,
    )
    if code_channel:
        await common.set_session_status(channel_id, "processing")
        if is_first_mention:
            await common.set_context_bar(
                channel_id,
                common.repo_context_bar_items(
                    repo_dict, dashboard_url=common.dashboard_thread_url(thread_id) or ""
                ),
            )
            await common.set_commands(channel_id, common.DEFAULT_CODE_CHANNEL_COMMANDS)
    try:
        run = await _dispatch_or_queue_slack_run(
            langgraph_client,
            thread_id,
            run_input,
            configurable,
            explicitly_tagged=explicitly_tagged,
            trigger_ts=event_ts,
        )
    except Exception:
        # No run means no completion webhook, so nothing else would ever clear
        # the loading UI this turn switched on.
        if code_channel:
            await common.set_session_status(channel_id, "active")
        raise
    common.logger.info(
        "Slack LangGraph run %s dispatched for thread %s",
        common.run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if code_channel and isinstance(run_id, str) and run_id:
        stream_thread_ts = reply_thread_ts or thread_ts
        await stream_slack_thinking_steps(
            client=langgraph_client,
            thread_id=thread_id,
            run_id=run_id,
            channel_id=channel_id,
            thread_ts=stream_thread_ts,
            mapping_thread_ts=thread_ts,
            original_message_ts=original_message_ts,
            recipient_user_id=user_id,
            recipient_team_id=request.team_id,
        )
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
    if not code_channel and isinstance(run_id, str) and run_id:
        await show_slack_thinking_status(
            client=langgraph_client,
            thread_id=thread_id,
            run_id=run_id,
            channel_id=channel_id,
            # A concierge DM names no Slack thread, so the status hangs on the
            # message being answered and the session owns only that one.
            thread_ts=(reply_thread_ts or original_message_ts) if concierge_mode else thread_ts,
            session_ts=thread_ts if concierge_mode else "",
        )
    return bool(isinstance(run_id, str) and run_id)
