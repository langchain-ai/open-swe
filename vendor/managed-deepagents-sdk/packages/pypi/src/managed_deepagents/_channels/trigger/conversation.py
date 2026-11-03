"""Start a new Trigger conversation from a run: a followed top-level post, and optionally a run.

With ``hand_off``, the new thread's conversation takes the run's own thread instead.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import TYPE_CHECKING, cast

from langgraph.config import get_config

from managed_deepagents._channels.run_input import normalize_run_input, run_start_options
from managed_deepagents._channels.runtime import require_text_content
from managed_deepagents._channels.trigger.ingress import source_thread_id, start_ingress_agent
from managed_deepagents._channels.trigger.transport import start_trigger_thread
from managed_deepagents._identity_runtime import _field
from managed_deepagents._managed_thread import current_run_thread
from managed_deepagents._schedule_auth import channel_run_execution
from managed_deepagents._thread_handoff import hand_off_thread

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.trigger.client import TriggerClientLike
    from managed_deepagents._channels.types import (
        ChannelMessage,
        ChannelTarget,
        JsonValue,
        PostedChannelThread,
    )
    from managed_deepagents._managed_thread import ManagedThread
    from managed_deepagents._schedule_auth import ExecutionUser

_ASSISTANT_ID_ENV = "MDA_ASSISTANT_ID"


async def start_trigger_conversation(
    client: TriggerClientLike,
    origin: ChannelBinding,
    message: ChannelMessage,
    run: ChannelRunResult | None,
    *,
    hand_off: bool = False,
    to: ChannelTarget | None = None,
) -> PostedChannelThread:
    """Post ``message`` at top level in the origin's conversation, and start ``run`` there.

    Trigger follows the new thread. The run starts as the current run's
    principal. With ``hand_off``, the new thread's conversation takes the
    current run's thread instead, as a scheduled run's post does. The input is
    checked before anything is posted.
    """
    text = require_text_content(message)
    conversation_id = _conversation_of(origin, to) if to is not None else None
    run_input = normalize_run_input(run)
    caller = _run_caller() if run_input is not None else None
    source = _hand_off_source() if hand_off else None
    action_id = str(uuid.uuid4())
    started = await start_trigger_thread(
        client,
        target=origin["target"],
        text=text,
        action_id=action_id,
        conversation_id=conversation_id,
    )
    if started is None:
        raise RuntimeError("Trigger did not name the new thread")
    key = started["source_thread_key"]
    thread_id = source_thread_id(key)
    if source is not None:
        thread, execution = source
        moved: ChannelBinding = {
            "name": origin["name"],
            "provider": origin["provider"],
            "source_thread_key": key,
            "delivery_id": action_id,
            "transport": "trigger_server",
            "target": cast("JsonValue", started),
        }
        await hand_off_thread(thread, execution, moved)
        # The rest of this run answers, and waits on decisions, in the new thread.
        if "reply" in origin:
            moved["reply"] = origin["reply"]
        current_run_thread(None, get_config().get("configurable", {})).handed_off.append(moved)
        thread_id = thread.id
    elif run_input is not None and caller is not None:
        user_id, assistant_id = caller
        options = run_start_options(run_input)
        await start_ingress_agent(
            thread_id=thread_id,
            assistant_id=assistant_id,
            messages=cast("list[dict[str, object]]", options["messages"]),
            state=options.get("state"),
            metadata=options.get("metadata"),
            webhook=options.get("webhook"),
            source_provider=origin["provider"],
            source_thread_key=key,
            user_id=user_id,
            context=dict(run_input.get("context", {})),
            binding={
                "name": origin["name"],
                "provider": origin["provider"],
                "source_thread_key": key,
                "delivery_id": action_id,
                "transport": "trigger_server",
                "event": {"type": "message", "messages": run_input["messages"]},
                "target": cast("JsonValue", started),
            },
        )
    message_id = str(started["address"]["thread_id"])
    posted: PostedChannelThread = {"id": message_id, "thread_id": thread_id}
    if origin["provider"] == "slack":
        conversation = started["address"]["conversation_id"]
        posted["url"] = f"https://slack.com/archives/{conversation}/p{message_id.replace('.', '')}"
    return posted


_SLACK_CONVERSATION_ID = re.compile(r"[CDG][A-Z0-9]+")


def _conversation_of(origin: ChannelBinding, to: ChannelTarget) -> str:
    """The conversation ``to`` names: another of the origin channel's, with no thread."""
    if to.provider != origin["provider"] or to.channel not in {None, origin["name"]}:
        raise ValueError("a new thread can go only to this channel's conversations")
    if to.provider != "slack":
        raise ValueError("a new thread can go to another conversation only on Slack")
    if to.thread_ts is not None:
        raise ValueError("a new thread's conversation names no thread")
    if to.code_channel:
        raise ValueError("a code channel is one conversation: it takes no new thread")
    if not _SLACK_CONVERSATION_ID.fullmatch(to.conversation_id):
        raise ValueError("a Slack conversation id looks like C…, D… or G…")
    return to.conversation_id


def _hand_off_source() -> tuple[ManagedThread, ExecutionUser]:
    """The current run's thread and the conversation principal that owns it."""
    configurable = get_config().get("configurable", {})
    execution = channel_run_execution(configurable)
    thread = current_run_thread(None, configurable).thread
    if execution is None or not thread.id:
        raise RuntimeError("only a channel conversation's run can hand off its thread")
    return thread, execution


def _run_caller() -> tuple[str, str]:
    """The current run's principal and assistant, which a new thread's run starts as."""
    config = get_config()
    configurable = config.get("configurable", {})
    user_id = _field(configurable.get("langgraph_auth_user"), "mda_agent_auth_principal_id")
    assistant_id = (
        configurable.get("assistant_id")
        or config.get("metadata", {}).get("assistant_id")
        or os.environ.get(_ASSISTANT_ID_ENV)
    )
    if not isinstance(user_id, str) or not user_id.strip():
        raise RuntimeError("a new thread's run needs the current run's principal")
    if not isinstance(assistant_id, str) or not assistant_id.strip():
        raise RuntimeError("a new thread's run needs the current run's assistant")
    return user_id.strip(), assistant_id.strip()
