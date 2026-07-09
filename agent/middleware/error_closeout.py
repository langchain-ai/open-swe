"""Error-closeout hook: notify the source surface when a root run crashes.

The langchain middleware ``after_agent`` hook only fires on a clean exit, so a
run that terminates with ``status=error`` mid-workflow (an unhandled exception
bubbling out of the graph) leaves null root output and posts nothing back to the
developer. This wrapper closes that gap: it wraps the compiled agent's
``astream``/``ainvoke`` and, on any non-cancellation error, posts a short honest
failure message to the originating surface before re-raising so the run still
records as errored.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.errors import GraphBubbleUp
from langgraph.pregel import Pregel

from ..utils.dashboard_handoff import DASHBOARD_HANDOFF_MARKER
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

_CLOSEOUT_SENT_FLAG = "_open_swe_error_closeout_sent"


def _config_get(config: Mapping[str, Any] | None, key: str) -> Any:
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    return configurable.get(key)


def _slack_thread(config: Mapping[str, Any] | None) -> tuple[str, str] | None:
    slack_thread = _config_get(config, "slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if isinstance(channel_id, str) and isinstance(thread_ts, str) and channel_id and thread_ts:
        return channel_id, thread_ts
    return None


def _content_contains(content: object, text: str) -> bool:
    if isinstance(content, str):
        return text in content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and text in str(block.get("text", "")):
                return True
    return False


def _is_web_handoff(messages: list[AnyMessage]) -> bool:
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "human":
            return _content_contains(getattr(msg, "content", ""), DASHBOARD_HANDOFF_MARKER)
    return False


def _failure_message(error: BaseException) -> str:
    detail = str(error).strip() or error.__class__.__name__
    return (
        "I hit an unrecoverable error and had to stop before finishing this task "
        f"({detail}). The workspace may be in a broken state and could need a reset. "
        "You can retry the request or ask me to recover from where I left off."
    )


def _input_messages(graph_input: Any) -> list[AnyMessage]:
    if isinstance(graph_input, Mapping):
        messages = graph_input.get("messages")
        if isinstance(messages, list):
            return messages
    return []


async def _post_closeout(
    graph: Pregel,
    graph_input: Any,
    config: Mapping[str, Any] | None,
    error: BaseException,
) -> None:
    """Post a failure closeout to Slack or the dashboard stream; never raise."""
    message = _failure_message(error)
    slack = _slack_thread(config)
    web_handoff = _is_web_handoff(_input_messages(graph_input))

    if slack is not None and not web_handoff:
        channel_id, thread_ts = slack
        try:
            await post_slack_thread_reply(channel_id, thread_ts, message)
            logger.info("Posted error closeout to Slack thread %s", thread_ts)
        except Exception:
            logger.exception("Failed to post error closeout to Slack")
        return

    try:
        await graph.aupdate_state(config, {"messages": [AIMessage(content=message)]})
        logger.info("Recorded error closeout as an inline assistant message")
    except Exception:
        logger.exception("Failed to record inline error closeout")


def _should_notify(error: BaseException) -> bool:
    """A healthy user cancel (CancelledError / graph interrupt) is not a crash."""
    return not isinstance(error, asyncio.CancelledError | GraphBubbleUp)


def wrap_agent_with_error_closeout(graph: Pregel) -> Pregel:
    """Wrap a compiled agent so root-run errors post a closeout to the source surface."""
    original_astream = graph.astream
    original_ainvoke = graph.ainvoke

    async def astream_with_closeout(
        graph_input: Any,
        config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        effective_config = config if config is not None else getattr(graph, "config", None)
        try:
            async for chunk in original_astream(graph_input, config, **kwargs):
                yield chunk
        except BaseException as error:  # noqa: BLE001 — re-raised after closeout
            if _should_notify(error) and not getattr(graph, _CLOSEOUT_SENT_FLAG, False):
                setattr(graph, _CLOSEOUT_SENT_FLAG, True)
                await _post_closeout(graph, graph_input, effective_config, error)
            raise

    async def ainvoke_with_closeout(
        graph_input: Any,
        config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        effective_config = config if config is not None else getattr(graph, "config", None)
        try:
            return await original_ainvoke(graph_input, config, **kwargs)
        except BaseException as error:  # noqa: BLE001 — re-raised after closeout
            if _should_notify(error) and not getattr(graph, _CLOSEOUT_SENT_FLAG, False):
                setattr(graph, _CLOSEOUT_SENT_FLAG, True)
                await _post_closeout(graph, graph_input, effective_config, error)
            raise

    graph.astream = astream_with_closeout  # type: ignore[method-assign]
    graph.ainvoke = ainvoke_with_closeout  # type: ignore[method-assign]
    return graph
