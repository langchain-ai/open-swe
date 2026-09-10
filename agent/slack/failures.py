"""Slack requests never end in silence.

Once an event is known to be addressed to Open SWE, every failure before the
agent answers is posted back to the same thread under a fresh error id, and the
same id is attached to the server log line so the failure can be looked up.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from agent.slack.client import post_slack_thread_reply
from agent.slack.responses import WebhookResponse, failed
from agent.utils.user_messages import warning

logger = logging.getLogger(__name__)

_UNEXPECTED_FAILURE = (
    "Open SWE hit an unexpected error and cannot respond to this message. Send it again to retry."
)


class SlackRequestError(Exception):
    """A request Open SWE will not act on; ``user_message`` is posted to the thread verbatim."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class SlackRequestTarget(BaseModel):
    """The thread that must hear back, and what to attach to the log line."""

    channel_id: str = ""
    thread_ts: str = ""
    event_id: str = ""
    agent_thread_id: str | None = None


async def report_slack_failure(target: SlackRequestTarget, exc: BaseException) -> str:
    """Log ``exc`` under a new error id, reply to the thread with that id, and return it."""
    error_id = str(uuid.uuid7())
    fields = {
        "error_id": error_id,
        "slack_channel_id": target.channel_id,
        "slack_thread_ts": target.thread_ts,
        "slack_event_id": target.event_id,
        "agent_thread_id": target.agent_thread_id,
    }
    if isinstance(exc, SlackRequestError):
        logger.warning("Slack request rejected", extra=fields)
        text = exc.user_message
    else:
        logger.error("Slack request failed", exc_info=exc, extra=fields)
        text = _UNEXPECTED_FAILURE
    if not target.channel_id or not target.thread_ts:
        logger.error("Slack failure has no thread to reply to", extra=fields)
        return error_id
    try:
        posted = await post_slack_thread_reply(
            target.channel_id,
            target.thread_ts,
            warning(f"{text}\nError ID: `{error_id}`"),
            agent_thread_id=target.agent_thread_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not post Slack failure reply", extra=fields)
        return error_id
    if not posted:
        logger.error("Slack failure reply was not delivered", extra=fields)
    return error_id


async def answer_slack_request(
    target: SlackRequestTarget, handle: Callable[[], Awaitable[WebhookResponse]]
) -> WebhookResponse:
    """Run a webhook handler for an addressed request; a failure becomes a thread reply.

    Slack sees 200 either way so it does not retry a request the user has
    already been told about.
    """
    try:
        return await handle()
    except Exception as exc:  # noqa: BLE001
        return failed(await report_slack_failure(target, exc))


async def run_slack_task(target: SlackRequestTarget, task: Awaitable[None]) -> None:
    """Background-task form of :func:`answer_slack_request`."""
    try:
        await task
    except Exception as exc:  # noqa: BLE001
        await report_slack_failure(target, exc)
